"""Deterministic external-operation simulation; no production cancellation claim."""

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


@pytest.mark.features("E1", "C2")
@pytest.mark.scenario("E1-PROVIDER-CANCELLATION-AUTHORIZATION")
async def test_cancellation_is_an_exact_separate_write(tmp_path):
    registry, policy = ToolRegistry(), ActionPolicy()
    journal = OperationStore(tmp_path / "operations.sqlite")
    executor = ToolExecutor(registry, policy, journal)
    entered, release = asyncio.Event(), asyncio.Event()
    mutations, cancellations = [], []

    async def create(payload, context):
        entered.set()
        await release.wait()
        if context.operation_id in cancellations:
            raise ToolError("synthetic_provider_canceled")
        mutations.append(context.operation_id)
        return {"provider_ref": context.operation_id}

    async def cancel(payload, context):
        cancellations.append(payload["operation_id"])
        release.set()
        return {"provider_ref": context.operation_id, "outcome": "request_accepted"}

    for account in ("a", "b"):
        registry.add_connection(Connection("jobs", account))
    write = Tool(
        "jobs",
        "a",
        "create",
        "Synthetic delayed job",
        {"type": "object"},
        {"type": "object"},
        create,
        action="write",
    )
    cancel_tool = Tool(
        "jobs",
        "a",
        "cancel",
        "Synthetic cancellation request",
        {
            "type": "object",
            "properties": {"operation_id": {"type": "string"}},
            "required": ["operation_id"],
            "additionalProperties": False,
        },
        {"type": "object"},
        cancel,
        action="write",
    )
    registry.register(write)
    registry.register(cancel_tool)
    write.cancel_tool = cancel_tool.key
    policy.set(Rule(write.key, "write", "allow"))
    policy.set(Rule(cancel_tool.key, "write", "confirm"))
    task = asyncio.create_task(
        executor.execute(write.key, {}, CallContext("s", 1, operation_id="original"))
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        context = CallContext("controls", 0, operation_id="cancel-request")
        denied = await executor.request_cancellation("original", context)
        assert denied["status"] == "confirmation_required" and cancellations == []
        proposal = await executor.request_cancellation("original", context, prepare=True)
        assert proposal["account"] == "a" and proposal["payload"] == {"operation_id": "original"}
        with pytest.raises(ToolError, match="invalid_confirmation"):
            executor.confirm(
                context.operation_id, "changed-payload-binding", input_kind="action_ui"
            )
        executor.confirm(context.operation_id, proposal["binding"], input_kind="action_ui")
        result = await executor.request_cancellation("original", context)
        assert result["status"] == "ok"
        assert journal.get(context.operation_id)["status"] == "succeeded"
        assert (await task)["status"] == "uncertain"
        assert mutations == [] and cancellations == ["original"]
        assert journal.get("original")["status"] == "uncertain"
        repeated = await executor.request_cancellation("original", context)
        assert repeated["duplicate"] and cancellations == ["original"]
        registry.connections[("jobs", "a")].disconnect()
        with pytest.raises(ToolError):
            await executor.request_cancellation(
                "original", CallContext("s", 2, operation_id="later")
            )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await executor.close()
        journal.close()
