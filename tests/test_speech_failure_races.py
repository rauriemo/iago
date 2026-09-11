"""Explicit scheduler barriers around failed speech and queued question ownership."""

import asyncio
from types import SimpleNamespace

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.core.ownership import HeardLedger
from reachy_brain.core.speech import SpeechStream
from reachy_brain.integrations.registry import ActionPolicy, ToolError, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C2", "C5", "C6")
@pytest.mark.scenario("SPEECH-FAILURE-QUEUED-PRODUCERS")
async def test_failed_consumer_releases_all_blocked_producers():
    entered, fail = asyncio.Event(), asyncio.Event()
    blocked = asyncio.Event()

    class ObservedQueue(asyncio.Queue):
        waiting = 0

        async def put(self, item):
            if self.full():
                self.waiting += 1
                if self.waiting == 5:
                    blocked.set()
            await super().put(item)

    class Voice:
        async def stream(self, text):
            entered.set()
            await fail.wait()
            raise OSError("synthetic")
            yield  # async generator, deliberately no audio on this failure path

    async def emit(*args, **kwargs):
        pass

    async def on_error(exc):
        pass

    stream = SpeechStream(
        0, lambda: True, emit, {"openai": Voice()}, "openai", HeardLedger(), 0, on_error=on_error
    )
    stream.queue = ObservedQueue(maxsize=2)
    async with stream:
        await stream.feed("Initial sentence.")
        await entered.wait()
        await stream.queue.put("Queued one.")
        await stream.queue.put("Queued two.")
        producers = [asyncio.create_task(stream._put(str(n))) for n in range(5)]
        # Explicitly reach queue backpressure before releasing the provider failure.
        await asyncio.wait_for(blocked.wait(), 1)
        fail.set()
        await asyncio.wait_for(asyncio.gather(*producers), 1)
        assert stream.failed


@pytest.mark.features("P10", "C2", "C8")
@pytest.mark.scenario("QUESTION-QUEUE-ACKNOWLEDGMENT-RACE")
@pytest.mark.parametrize("cause", ["stop", "failure", "replacement"])
async def test_question_cannot_report_queued_after_ownership_changes(cause):
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(event):
        pass

    async def question(text, identity):
        entered.set()
        await release.wait()

    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.visual.source(core.connection, "camera", "Synthetic")
    core.speech = SimpleNamespace(failed=False, question=question)
    request = asyncio.create_task(core.ask_yes_no("Should we continue?"))
    await entered.wait()
    replacement = None
    if cause == "stop":
        await core.stop()
    elif cause == "failure":
        core.speech.failed = True
        core.question_draft = None
    else:
        replacement = {"id": "new-question"}
        core.question_draft = replacement
    release.set()
    with pytest.raises(ToolError, match="question_canceled_before_queue_acknowledgment"):
        await request
    assert core.thumbs.question is None
    assert core.question_draft is replacement
