"""Real controller stop/commit awaits with synthetic gesture evidence."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from reachy_brain.behavior.thumbs import Observation, Question
from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


def candidate(core, camera):
    at = time.time()
    core.thumbs.present(
        Question("q", core.session, core.epoch, camera.id, camera.generation, at, at + 15)
    )
    for i, (delta, gesture) in enumerate(
        [(0, "neutral"), (0.31, "neutral"), (0.4, "thumb_up"), (0.8, "thumb_up")]
    ):
        core.thumbs.observe(
            Observation(
                str(i), camera.id, camera.generation, "camera", at + delta, gesture, 0.95, 1, True
            ),
            now=at + delta,
        )
    return core.thumbs.poll(now=at + 1.06)


@pytest.mark.features("P10", "C2", "D5")
@pytest.mark.scenario("THUMB-COMMIT-AWAIT-INVALIDATION")
@pytest.mark.parametrize(
    "change",
    [
        "clear",
        "idle",
        "turn",
        "speech",
        "session",
        "old_turn",
        "disable",
        "question",
        "source_kind",
    ],
)
async def test_thumb_cannot_commit_after_context_changes_during_stop(change):
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(message):
        if message["type"] == "stop":
            entered.set()
            await release.wait()

    core = Conversation(
        Settings(_env_file=None, thumb_responses_enabled=True),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.answer = AsyncMock()
    camera = core.visual.source(core.connection, "camera", "Synthetic")
    response = candidate(core, camera)
    if change == "old_turn":
        core.epoch += 1
        release.set()
    task = asyncio.create_task(core.accept_thumb(response))
    try:
        if change != "old_turn":
            await asyncio.wait_for(entered.wait(), 2)
            if change == "clear":
                core.visual.clear(camera.id, disable=True)
            if change == "idle":
                core.mode = "idle"
            if change == "turn":
                core.epoch += 1
            if change == "speech":
                core.user_speaking = True
            if change == "session":
                core.session = "replacement"
            if change == "disable":
                core.thumbs.enabled = False
            if change == "question":
                core.question_draft = {"id": "replacement"}
            if change == "source_kind":
                camera.kind = "screen"
            release.set()
        await asyncio.wait_for(task, 2)
        await asyncio.sleep(0)
        assert not any(row["role"] == "user" for row in core.history)
        assert not core.gesture_slots
        assert core.gesture_timings.snapshot()["total"] == 0
        core.answer.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await core.stop()
        await core.executor.close()


@pytest.mark.features("P10", "D5")
@pytest.mark.scenario("THUMB-COMMIT-LIVE-SOURCE-BOUNDARY")
@pytest.mark.parametrize("kind", ["screen", "upload", "historical"])
async def test_non_camera_source_cannot_commit_a_thumb_response(kind):
    core = Conversation(
        Settings(_env_file=None, thumb_responses_enabled=True),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        AsyncMock(),
    )
    core.mode = "conversation"
    core.answer = AsyncMock()
    source = core.visual.source(core.connection, "camera", "Synthetic")
    response = candidate(core, source)
    source.kind = kind
    epoch = core.epoch
    try:
        await core.accept_thumb(response)
        if core.task:
            await core.task
        assert core.epoch == epoch
        assert not core.gesture_slots
        assert core.gesture_activity.snapshot()["counts"]["accepted"] == 0
        core.answer.assert_not_called()
    finally:
        await core.stop()
        await core.executor.close()


@pytest.mark.features("P10", "C2", "C8")
@pytest.mark.scenario("THUMB-COMMIT-TRANSCRIPT-SPEECH-RACE")
async def test_delayed_transcript_delivery_cannot_restore_superseded_gesture():
    entered, release = asyncio.Event(), asyncio.Event()
    recorded = []

    async def send(message):
        if message["type"] == "transcript":
            entered.set()
            await release.wait()

    core = Conversation(
        Settings(_env_file=None, thumb_responses_enabled=True),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.answer = AsyncMock()
    core.record = lambda *args, **kwargs: recorded.append((args, kwargs))
    camera = core.visual.source(core.connection, "camera", "Synthetic")
    response = candidate(core, camera)
    task = asyncio.create_task(core.accept_thumb(response))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await core.speech_onset(response["start"])
        release.set()
        await asyncio.wait_for(task, 2)
        await asyncio.sleep(0)
        gesture_records = [
            (args, kw)
            for args, kw in recorded
            if len(args) > 1 and args[1] == "gesture-" + response["slot"]
        ]
        assert len(gesture_records) == 2
        assert not gesture_records[0][1].get("remove")
        assert gesture_records[1][1].get("remove") is True
        assert not any(row["role"] == "user" for row in core.history)
        core.answer.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await core.stop()
        await core.executor.close()
