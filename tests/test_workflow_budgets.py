"""Controlled clocks and real executor verify separate workflow/retrieval bounds."""

import json
import time
from types import SimpleNamespace

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import (
    ActionPolicy,
    Connection,
    Rule,
    Tool,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("E1", "V3", "V6", "K1")
@pytest.mark.scenario("WORKFLOW-RETRIEVAL-BUDGET-ISOLATION")
@pytest.mark.parametrize(
    "modules,advance,expected,limited",
    [
        (["calendar"] * 5, 3, ["calendar"] * 5, 0),
        (["visual"] * 4, 0, ["visual"] * 3, 1),
        (
            ["visual", "calendar", "documents", "calendar", "visual", "documents"],
            0,
            ["visual", "calendar", "documents", "calendar", "visual"],
            1,
        ),
        (["calendar", "visual"], 6, ["calendar"], 1),
        (["calendar"] * 2, 31, ["calendar"], 0),
        (["calendar"] * 9, 0, ["calendar"] * 7, 0),
    ],
)
async def test_workflows_do_not_expand_retrieval_bounds(
    monkeypatch, modules, advance, expected, limited
):
    now = [0.0]
    monkeypatch.setattr(
        "reachy_brain.core.conversation.time",
        SimpleNamespace(
            monotonic=lambda: now[0],
            time=time.time,
        ),
    )
    registry, policy = ToolRegistry(), ActionPolicy()
    dispatched, outputs, spoken = [], [], []
    for module in {"calendar", "visual", "documents"}:
        registry.add_connection(Connection(module, "test"))

        async def handler(payload, context, module=module):
            dispatched.append(module)
            return {"source": module}

        tool = Tool(
            module,
            "test",
            "read",
            "Synthetic fixture read",
            {"type": "object"},
            {"type": "object"},
            handler,
        )
        registry.register(tool)
        policy.set(Rule(tool.key, "read", "allow"))

    class Brain:
        calls = 0

        async def stream(self, messages, tools):
            outputs[:] = [
                json.loads(m["output"]) for m in messages if m.get("type") == "function_call_output"
            ]
            now[0] += advance
            index = self.calls
            self.calls += 1
            if index < len(modules):
                yield {
                    "type": "item",
                    "item": {
                        "type": "function_call",
                        "name": f"{modules[index]}__test__read",
                        "call_id": str(index),
                        "arguments": "{}",
                    },
                }
            else:
                yield {"type": "text", "text": "Finished synthetic workflow."}

    class Speech:
        async def feed(self, text):
            spoken.append(text)

    async def send(message):
        pass

    executor = ToolExecutor(registry, policy, None)
    brain = Brain()
    core = Conversation(Settings(_env_file=None), brain, {}, executor, VisualStore(), send)
    core.mode = "conversation"
    try:
        await core._answer(0, Speech())
        assert dispatched == expected
        assert sum(o["status"] == "retrieval_limit" for o in outputs) == limited
        assert brain.calls <= 8
        if len(modules) > 7 or advance == 31:
            assert any("work limit" in text for text in spoken)
        else:
            assert spoken[-1] == "Finished synthetic workflow."
    finally:
        await executor.close()


@pytest.mark.features("E1", "V3", "K1")
@pytest.mark.scenario("WORKFLOW-APPROVAL-DEADLINE-ISOLATION")
@pytest.mark.parametrize("module", ["calendar", "visual", "documents"])
async def test_approval_time_cannot_extend_retrieval(monkeypatch, module):
    now = [0.0]
    monkeypatch.setattr(
        "reachy_brain.core.conversation.time",
        SimpleNamespace(
            monotonic=lambda: now[0],
            time=time.time,
        ),
    )
    registry, policy = ToolRegistry(), ActionPolicy()
    registry.add_connection(Connection(module, "test"))
    dispatched, outputs = [], []

    async def handler(payload, context):
        dispatched.append(module)
        return {"source": module}

    tool = Tool(
        module,
        "test",
        "read",
        "Synthetic read requiring confirmation",
        {"type": "object"},
        {"type": "object"},
        handler,
    )
    registry.register(tool)
    policy.set(Rule(tool.key, "read", "confirm"))
    executor = ToolExecutor(registry, policy, None)

    class Brain:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield {
                    "type": "item",
                    "item": {
                        "type": "function_call",
                        "name": tool.key,
                        "call_id": "approval",
                        "arguments": "{}",
                    },
                }
            else:
                outputs.extend(
                    json.loads(m["output"])
                    for m in messages
                    if m.get("type") == "function_call_output"
                )
                yield {"type": "text", "text": "Synthetic result."}

    class Speech:
        async def feed(self, text):
            pass

    async def send(message):
        if message["type"] == "confirmation":
            now[0] += 20  # Controlled delay below the independent 60-second approval expiry.
            proposal = message["proposal"]
            executor.confirm(proposal["operation_id"], proposal["binding"], input_kind="action_ui")
            core.confirmations[proposal["operation_id"]].set_result(True)

    core = Conversation(
        Settings(_env_file=None, workflow_deadline_seconds=10),
        Brain(),
        {},
        executor,
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    try:
        await core._answer(0, Speech())
        assert dispatched == (["calendar"] if module == "calendar" else [])
        assert outputs[0]["status"] == ("ok" if module == "calendar" else "retrieval_limit")
        assert not executor.pending and not core.confirmations
    finally:
        await executor.close()
