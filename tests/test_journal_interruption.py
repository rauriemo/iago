"""Synthetic provider mutations and held SQLite calls; Stop must remain independent."""

import asyncio
import threading

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Connection,
    Rule,
    Tool,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("E1", "C2", "D5")
@pytest.mark.scenario("E1-JOURNAL-INTERRUPTION-FENCE")
@pytest.mark.parametrize("stage", ["before_dispatch", "known_success", "proposal"])
async def test_stop_during_journal_work_does_not_dispatch_or_erase_known_success(tmp_path, stage):
    journal = OperationStore(tmp_path / "operations.sqlite")
    registry, policy = ToolRegistry(), ActionPolicy()
    changes, sent = [], []

    async def provider(payload, context):
        changes.append(context.operation_id)
        return {"provider_ref": "synthetic-result"}

    registry.add_connection(Connection("fixture", "a"))
    tool = Tool(
        "fixture",
        "a",
        "write",
        "Synthetic write",
        {"type": "object"},
        {"type": "object"},
        provider,
        action="write",
    )
    registry.register(tool)
    policy.set(Rule(tool.key, "write", "confirm" if stage == "proposal" else "allow"))
    executor = ToolExecutor(registry, policy, journal)

    async def send(message):
        sent.append(message)

    core = Conversation(Settings(_env_file=None), None, {}, executor, VisualStore(), send)
    core.mode = "conversation"
    context = CallContext(core.session, 0, valid=lambda: core.valid(0), operation_id="held-write")
    entered, release = threading.Event(), threading.Event()
    original = (
        journal.prepare
        if stage == "proposal"
        else journal.begin
        if stage == "before_dispatch"
        else journal.finish
    )

    def held(*args):
        if stage in {"before_dispatch", "proposal"} or args[1] == "succeeded":
            entered.set()
            assert release.wait(3)
        return original(*args)

    if stage == "proposal":
        journal.prepare = held
    elif stage == "before_dispatch":
        journal.begin = held
    else:
        journal.finish = held
    core.task = asyncio.create_task(
        (executor.propose if stage == "proposal" else executor.execute)(tool.key, {}, context)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.wait_for(core.stop(), 0.1)
        assert sent[-1]["type"] == "stop"
        assert not core.task.done()
        closing = asyncio.create_task(executor.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await core.task
        await closing
        assert not executor.executions
        result = journal.get(context.operation_id)
        if stage in {"before_dispatch", "proposal"}:
            assert changes == [] and result["status"] == "canceled-before-dispatch"
        else:
            assert changes == [context.operation_id] and result["status"] == "succeeded"
            assert result["provider_ref"] == "synthetic-result"
    finally:
        release.set()
        await asyncio.gather(core.task, return_exceptions=True)
        journal.close()
