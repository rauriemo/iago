"""Synthetic summarizer checks retention/provenance; summary quality requires live evals."""

import asyncio
from collections import deque

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C8", "C2")
@pytest.mark.scenario("CONVERSATION-SUMMARY-BOUNDARIES")
@pytest.mark.parametrize("outcome", ["success", "oversize", "empty", "stop", "clear", "gesture"])
async def test_only_canonical_history_is_compacted_and_canceled_summary_cannot_return(outcome):
    events, inputs, closed = [], [], []

    class Brain:
        async def stream(self, messages, tools):
            inputs.append(messages)
            assert tools == []
            try:
                if outcome == "stop":
                    await core.stop()
                elif outcome == "clear":
                    await core.clear_evidence()
                yield {
                    "type": "text",
                    "text": "x" * 3001
                    if outcome == "oversize"
                    else ""
                    if outcome == "empty"
                    else "User chose the blue design; cost remains unresolved.",
                }
            finally:
                closed.append(True)

    async def send(event):
        events.append(event)

    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.history = deque(
        [
            {
                "role": "user" if n % 2 == 0 else "assistant",
                "content": f"Canonical heard message {n}",
            }
            for n in range(26)
        ],
        maxlen=40,
    )
    original = list(core.history)
    core.answer_generated = "UNHEARD GENERATED SECRET SENTENCE"
    if outcome == "gesture":
        core.gesture_slots["pending"] = original[0]
    await core.compact_history(core.epoch)
    assert "UNHEARD GENERATED" not in str(inputs)
    if outcome == "gesture":
        assert not inputs and list(core.history) == original
    else:
        assert closed == [True]
        assert "Canonical heard message 17" in inputs[0][1]["content"]
        assert "Canonical heard message 18" not in inputs[0][1]["content"]
        if outcome == "success":
            assert len(core.history) == 9
            assert list(core.history)[1:] == original[-8:]
            assert "derived memory" in core.history[0]["content"]
            await core.clear_evidence()
            assert not core.history
        elif outcome == "clear":
            assert not core.history
        else:
            assert list(core.history) == original
            assert not any(e.get("status") == "summarized" for e in events)


@pytest.mark.features("C8", "C2")
@pytest.mark.scenario("CONVERSATION-SUMMARY-CANCELLATION")
async def test_summary_task_cancellation_closes_provider_without_changing_history():
    entered, closed = asyncio.Event(), asyncio.Event()

    class Brain:
        async def stream(self, messages, tools):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield
            finally:
                closed.set()

    async def send(event):
        pass

    core = Conversation(Settings(_env_file=None), Brain(), {}, None, None, send)
    core.mode = "conversation"
    core.history.extend({"role": "user", "content": str(n)} for n in range(26))
    original = list(core.history)
    task = asyncio.create_task(core.compact_history(0))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and list(core.history) == original
