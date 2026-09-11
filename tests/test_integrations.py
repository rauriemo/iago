"""E1 policy and failure scenarios using explicitly synthetic provider state."""

import asyncio

import pytest

from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Connection,
    Rule,
    Tool,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)


def install_lookup(registry, policy, tool, callback):
    async def handler(payload, context):
        return await callback(payload["operation_id"], context)

    lookup = Tool(
        tool.module,
        tool.account,
        "operation_status",
        "Synthetic status lookup",
        {
            "type": "object",
            "properties": {"operation_id": {"type": "string"}},
            "required": ["operation_id"],
        },
        {"type": "object"},
        handler,
    )
    registry.register(lookup)
    policy.set(Rule(lookup.key, "read", "allow"))
    tool.reconcile_tool = lookup.key


@pytest.fixture
def system(tmp_path):
    registry, policy = ToolRegistry(), ActionPolicy()
    journal = OperationStore(tmp_path / "operations.sqlite")
    changes = []

    async def create(payload, ctx):
        changes.append((ctx.operation_id, payload["title"]))
        return {"provider_ref": "synthetic-1"}

    for account in ["a", "b"]:
        registry.add_connection(Connection("calendar", account, {"events.write"}))
        registry.register(
            Tool(
                "calendar",
                account,
                "create",
                "Create a fake event",
                {
                    "type": "object",
                    "properties": {"title": {"type": "string", "maxLength": 100}},
                    "required": ["title"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {"provider_ref": {"type": "string"}},
                    "required": ["provider_ref"],
                    "additionalProperties": False,
                },
                create,
                action="write",
                scopes=frozenset({"events.write"}),
            )
        )
    policy.set(Rule("calendar__a__create", "write", "confirm"))
    executor = ToolExecutor(registry, policy, journal)
    yield registry, policy, journal, executor, changes
    journal.close()


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-ACCOUNT-IDENTITY-CONFIRMATION")
async def test_identity_change_invalidates_approved_payload(system):
    reg, policy, journal, executor, changes = system
    context = CallContext("s", 1, operation_id="identity-change")
    payload = {"title": "Synthetic approval"}
    key = "calendar__a__create"
    proposal = await executor.propose(key, payload, context)
    executor.confirm(context.operation_id, proposal["binding"], input_kind="action_ui")
    reg.connections[("calendar", "a")].identity = "replacement-account"
    assert (await executor.execute(key, payload, context))["status"] == "operation_conflict"
    assert not changes
    await executor.close()
    assert journal.get(context.operation_id)["status"] == "canceled-before-dispatch"


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-CONFIRM")
async def test_exact_confirmation_and_duplicate(system):
    reg, policy, journal, executor, changes = system
    ctx = CallContext("s", 1, operation_id="op1")
    payload = {"title": "Synthetic review"}
    proposal = await executor.propose("calendar__a__create", payload, ctx)
    assert journal.get("op1")["status"] == "awaiting-confirmation"
    for kind in ["wave", "presence", "thumb", "stale_thumb"]:
        with pytest.raises(ToolError):
            executor.confirm("op1", proposal["binding"], input_kind=kind)
    executor.confirm("op1", proposal["binding"], input_kind="action_ui")
    altered = await executor.execute("calendar__a__create", {"title": "Changed"}, ctx)
    assert altered["status"] == "operation_conflict"
    first = await executor.execute("calendar__a__create", payload, ctx)
    second = await executor.execute("calendar__a__create", payload, ctx)
    assert first["status"] == "ok" and second["duplicate"]
    assert len(changes) == 1
    assert journal.get("op1")["status"] == "succeeded"
    assert (await executor.execute("calendar__a__create", {"title": "Changed"}, ctx))[
        "status"
    ] == "operation_conflict"


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-ACCOUNTS")
async def test_accounts_disabled_and_invalid_input(system):
    reg, policy, journal, executor, changes = system
    ctx = CallContext("s", 1, operation_id="op1")
    assert [t["name"] for t in reg.discover(policy, ctx)] == ["calendar__a__create"]
    assert (await executor.execute("calendar__b__create", {"title": "No"}, ctx))[
        "status"
    ] == "denied"
    assert (await executor.execute("calendar__a__create", {"title": 4}, ctx))[
        "status"
    ] == "invalid_input"
    reg.tools["calendar__a__create"].enabled = False
    assert not reg.discover(policy, ctx)
    assert (await executor.execute("calendar__a__create", {"title": "No"}, ctx))[
        "status"
    ] == "disabled"
    assert not changes


@pytest.mark.features("E1", "C2")
@pytest.mark.scenario("E1-LIFECYCLE")
async def test_concurrent_duplicates_and_cancel_after_dispatch(system):
    reg, policy, journal, executor, changes = system
    policy.set(Rule("calendar__a__create", "write", "allow"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(payload, ctx):
        changes.append((ctx.operation_id, payload["title"]))
        entered.set()
        await release.wait()
        return {"provider_ref": "fake"}

    reg.tools["calendar__a__create"].handler = slow
    ctx = CallContext("s", 1, operation_id="race")
    task = asyncio.create_task(executor.execute("calendar__a__create", {"title": "Once"}, ctx))
    await entered.wait()
    duplicate = await executor.execute("calendar__a__create", {"title": "Once"}, ctx)
    assert duplicate["duplicate"] and len(changes) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert journal.get("race")["status"] == "uncertain"
    assert (await executor.execute("calendar__a__create", {"title": "Once"}, ctx))[
        "status"
    ] == "uncertain"
    assert len(changes) == 1


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-RECONCILE")
async def test_committed_lost_response_reconciliation(system):
    reg, policy, journal, executor, changes = system
    policy.set(Rule("calendar__a__create", "write", "allow"))

    async def lost(payload, ctx):
        changes.append((ctx.operation_id, payload["title"]))
        raise ConnectionError("synthetic response lost")

    async def lookup(op, ctx):
        return {"status": "succeeded", "provider_ref": "fake-committed"}

    tool = reg.tools["calendar__a__create"]
    tool.handler = lost
    install_lookup(reg, policy, tool, lookup)
    ctx = CallContext("s", 1, operation_id="lost")
    assert (await executor.execute(tool.key, {"title": "Once"}, ctx))["status"] == "uncertain"
    result = await executor.reconcile("lost", ctx)
    assert result["status"] == "succeeded"
    assert len(changes) == 1


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-RECONCILIATION-OWNERSHIP")
async def test_reconciliation_cannot_race_dispatch_or_another_lookup(system):
    reg, policy, journal, executor, changes = system
    tool = reg.tools["calendar__a__create"]
    entered, release = asyncio.Event(), asyncio.Event()
    lookups = []

    async def lookup(op, context):
        lookups.append(op)
        entered.set()
        await release.wait()
        return {"status": "succeeded", "provider_ref": "synthetic-confirmed"}

    install_lookup(reg, policy, tool, lookup)
    context = CallContext("s", 1, operation_id="recover")
    journal.begin(
        "recover",
        "synthetic-digest",
        tool.key,
        0,
        executor.account_ref(reg.connections[(tool.module, tool.account)]),
    )
    assert (await executor.reconcile("recover", context))["status"] == "dispatched"
    assert not lookups
    journal.finish("recover", "uncertain")
    first = asyncio.create_task(executor.reconcile("recover", context))
    try:
        await entered.wait()
        second = await executor.reconcile("recover", context)
        assert second["status"] == "reconciling" and lookups == ["recover"]
        release.set()
        assert (await first)["status"] == "succeeded"
        assert (await executor.reconcile("recover", context))["status"] == "succeeded"
        assert lookups == ["recover"] and not changes
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-RECONCILIATION-CANCEL-RESTART")
async def test_canceled_lookup_and_restart_keep_uncertainty(system):
    reg, policy, journal, executor, changes = system
    tool = reg.tools["calendar__a__create"]
    entered = asyncio.Event()

    async def lookup(op, context):
        entered.set()
        await asyncio.Event().wait()

    install_lookup(reg, policy, tool, lookup)
    journal.begin(
        "recover",
        "synthetic-digest",
        tool.key,
        0,
        executor.account_ref(reg.connections[(tool.module, tool.account)]),
    )
    journal.finish("recover", "uncertain")
    task = asyncio.create_task(executor.reconcile("recover", CallContext("s", 1)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert journal.get("recover")["status"] == "uncertain"
    assert journal.claim_reconciliation("recover")
    journal.close()
    reopened = OperationStore(journal.path)
    try:
        assert reopened.get("recover")["status"] == "uncertain"
        assert not changes
    finally:
        reopened.close()


@pytest.mark.features("E1", "C2")
@pytest.mark.scenario("E1-DURABLE-EXECUTOR-QUEUE")
@pytest.mark.parametrize("stop_kind", ["cancel", "revoke", "operation_control"])
async def test_queued_write_is_durable_deduplicated_and_cancellable(system, stop_kind):
    reg, policy, journal, executor, changes = system
    tool = reg.tools["calendar__a__create"]
    policy.set(Rule(tool.key, "write", "allow"))
    executor.slots = asyncio.Semaphore(1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(payload, context):
        changes.append(context.operation_id)
        entered.set()
        await release.wait()
        return {"provider_ref": "synthetic-held"}

    tool.handler = held
    first = asyncio.create_task(
        executor.execute(tool.key, {"title": "First"}, CallContext("s", 1, operation_id="first"))
    )
    second = None
    try:
        await entered.wait()
        context = CallContext("s", 2, operation_id="queued")
        second = asyncio.create_task(executor.execute(tool.key, {"title": "Second"}, context))
        async with asyncio.timeout(2):
            while True:
                record = await executor.journal(journal.get, "queued")
                if record and record["status"] == "queued":
                    break
                await asyncio.sleep(0.01)
        assert len(record["authorization_ref"]) == 64
        duplicate = await executor.execute(tool.key, {"title": "Second"}, context)
        assert duplicate["duplicate"] and duplicate["status"] == "queued"
        assert changes == ["first"]
        if stop_kind == "cancel":
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
        elif stop_kind == "operation_control":
            result = await executor.cancel_undispatched("queued")
            assert result["canceled"]
            # An already dispatched call is not falsely reported as canceled.
            assert not (await executor.cancel_undispatched("first"))["canceled"]
            release.set()
            assert (await second)["status"] == "canceled-before-dispatch"
        else:
            policy.set(Rule(tool.key, "write", "deny"))
            release.set()
            assert (await second)["status"] == "authorization_changed"
        assert journal.get("queued")["status"] == "canceled-before-dispatch"
        assert changes == ["first"]
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)


@pytest.mark.features("E1", "C2")
@pytest.mark.scenario("E1-DURABLE-PROPOSAL-CANCELLATION")
@pytest.mark.parametrize("reason", ["stop", "expire", "reject"])
async def test_unapproved_proposal_is_durably_canceled(system, reason):
    reg, policy, journal, executor, changes = system
    clock = [100.0]
    executor.clock = lambda: clock[0]
    context = CallContext("session", 3, operation_id="proposal")
    proposal = await executor.propose(
        "calendar__a__create", {"title": "Synthetic private title"}, context
    )
    assert journal.get("proposal")["status"] == "awaiting-confirmation"
    assert b"Synthetic private title" not in journal.path.read_bytes()
    if reason == "stop":
        executor.cancel_pending("session", 3)
    elif reason == "expire":
        clock[0] = 161
        executor._expire()
    else:
        executor.drop_proposal("proposal")
    await asyncio.gather(*executor.cancellations)
    assert journal.get("proposal")["status"] == "canceled-before-dispatch"
    with pytest.raises(ToolError, match="invalid_confirmation"):
        executor.confirm("proposal", proposal["binding"], input_kind="action_ui")
    assert not changes
