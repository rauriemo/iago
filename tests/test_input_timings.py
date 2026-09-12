"""Synthetic monotonic timestamps around actual recognition handoff; no provider latency claim."""

import asyncio
import json

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.core.timing import InputTimings, ResponseTimings
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("INPUT-TIMINGS-REORDERED-CONTROLLER-HANDOFF")
async def test_late_first_transcript_preserves_per_input_measurements():
    now = [0.0]

    async def send(event):
        pass

    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.timings = ResponseTimings(clock=lambda: now[0])
    core.mode = "conversation"

    async def no_answer(epoch):
        pass

    core.answer = no_answer

    class STT:
        async def commit(self):
            return True

    await core.commit_recognition(STT())
    now[0] = 1
    await core.commit_recognition(STT())
    for at, item in ((2, "private-old-id"), (3, "private-new-id")):
        now[0] = at
        await core.transcription_event(dict(type="input_audio_buffer.committed", item_id=item))
    for at, item in ((4, "private-new-id"), (6, "private-old-id")):
        now[0] = at
        await core.transcription_event(
            dict(
                type="conversation.item.input_audio_transcription.completed",
                item_id=item,
                transcript="Private synthetic utterance",
            )
        )
    await core.task
    rows = core.timings.snapshot()["samples"]
    assert [r["recognition"] for r in rows] == [
        dict(
            status="measured",
            commit_to_ack_seconds=2,
            commit_to_transcript_seconds=6,
            commit_to_release_seconds=6,
            reorder_wait_seconds=0,
        ),
        dict(
            status="measured",
            commit_to_ack_seconds=2,
            commit_to_transcript_seconds=3,
            commit_to_release_seconds=5,
            reorder_wait_seconds=2,
        ),
    ]
    assert "private" not in json.dumps(rows).lower()
    assert not core.input_timings.commits and not core.input_timings.items
    await core.stop()
    await core.executor.close()


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("INPUT-TIMINGS-BOUNDS-AND-MISSING-CORRELATION")
def test_early_completion_bounds_and_invalid_timelines():
    now = [0.0]
    timings = InputTimings(clock=lambda: now[0])
    timings.start("commit")
    now[0] = 1
    timings.complete("item")
    now[0] = 2
    timings.bind("item", "commit")
    now[0] = 3
    assert timings.release("item") == dict(
        status="measured",
        commit_to_ack_seconds=2,
        commit_to_transcript_seconds=1,
        commit_to_release_seconds=3,
        reorder_wait_seconds=2,
    )
    assert timings.release("unknown") == dict(status="unavailable")
    for i in range(100):
        timings.start(str(i))
        timings.complete(str(i))
    assert len(timings.commits) == 32 and len(timings.items) == 64
    timings.bind("0", "0")
    assert timings.release("0") == dict(status="unavailable")
    timings.clear()
    assert not timings.commits and not timings.items
    timings.start("backwards")
    now[0] = 2
    timings.bind("backwards", "backwards")
    timings.complete("backwards")
    assert timings.release("backwards") == dict(status="invalid_timeline")


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("INPUT-TIMINGS-COMMIT-FAILURE-CLEANUP")
@pytest.mark.parametrize("outcome", ["empty", "error", "cancel"])
async def test_noncommitted_audio_leaves_no_timing_correlation(outcome):
    async def send(event):
        pass

    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )

    class STT:
        async def commit(self):
            if outcome == "error":
                raise OSError("synthetic")
            if outcome == "cancel":
                raise asyncio.CancelledError()
            return False

    if outcome == "empty":
        assert await core.commit_recognition(STT()) is False
    else:
        with pytest.raises(OSError if outcome == "error" else asyncio.CancelledError):
            await core.commit_recognition(STT())
    assert not core.input_timings.commits and not core.recording_commits
    await core.executor.close()
