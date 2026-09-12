"""Real RobotSession event handler/controller with synthetic uncertain clock mappings."""

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.robot.session import RobotSession
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C1", "C2", "C3", "D2", "D3")
@pytest.mark.scenario("ROBOT-UNCERTAIN-CONTINUATION-ADMISSION")
@pytest.mark.parametrize(
    "mapping",
    [
        None,
        {"time": 200, "stale": True, "uncertainty": 0.01},
        {"time": 200, "stale": False, "uncertainty": 0.2},
    ],
)
async def test_uncertain_robot_onset_holds_older_answer_until_ordered_commit(mapping):
    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        AsyncMock(),
    )
    core.mode = "conversation"
    answers = []

    async def answer(epoch):
        answers.append([row["content"] for row in core.history])

    core.answer = answer
    recognition = SimpleNamespace(commit=AsyncMock(return_value=True))
    session = SimpleNamespace(
        core=core,
        stt=recognition,
        speech_event_ids=deque(maxlen=128),
        edge=SimpleNamespace(clock=SimpleNamespace(local=lambda captured, now: mapping)),
    )
    try:
        await core.speech_onset(100.0)
        await core.commit_recognition(recognition, capture_end=101.0)
        await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": "a"})
        await RobotSession.event(session, {"type": "stop", "generation": 1})
        onset = {"type": "speech_start", "sequence": 1, "captured": 200.0}
        await RobotSession.event(session, onset)
        await RobotSession.event(session, onset, from_audio=True)
        await core.transcription_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "a",
                "transcript": "First condition",
            }
        )
        await asyncio.sleep(0)
        assert answers == [], "Unknown capture time allowed an older answer during speech"
        assert core.user_speaking and core.speech_inputs_pending()
        assert core.input_capture_start is None
        assert core.speech_activity.snapshot()["counts"]["uncertain_onset"] == 1
        end = {"type": "speech_end", "sequence": 2, "captured": 201.0}
        await RobotSession.event(session, end)  # Unordered control event must not commit.
        assert recognition.commit.await_count == 1
        assert core.speech_inputs_pending()
        await RobotSession.event(session, end, from_audio=True)
        assert recognition.commit.await_count == 2
        assert core.recording_commits[-1][2] == {}  # No invented capture interval.
        await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": "b"})
        await core.transcription_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "b",
                "transcript": "Second condition",
            }
        )
        await asyncio.sleep(0)
        assert answers == [["First condition", "Second condition"]]
        assert not core.user_speaking and not core.speech_inputs_pending()
        await core.set_mode("aware")
        assert not core.speech_inputs_pending()
    finally:
        await core.stop()
        await core.executor.close()


@pytest.mark.features("C2", "V5", "D2", "D3")
@pytest.mark.scenario("UNCERTAIN-ONSET-REFERENCE-OWNERSHIP")
async def test_uncertain_onset_consumes_reference_without_inventing_alignment():
    from test_spoken_reference import STT, controller

    core, frames, turns, events = controller()
    try:
        core.select_speech_reference(frames[0].id)
        await core.uncertain_speech_onset()
        assert core.pending_visual_reference is None
        assert core.input_visual_reference["onset_missing"]
        # A later mapped onset before the ordered commit cannot retroactively
        # validate the previously uncertain reference association.
        await core.speech_onset(101.0)
        await core.commit_recognition(STT(), capture_end=102.0)
        await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": "a"})
        await core.transcription_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "a",
                "transcript": "Read the selected image",
            }
        )
        assert turns == []
        assert any(e["type"] == "error" for e in events)
        assert not core.reference_inputs and core.pending_visual_reference is None
        assert not core.user_speaking and not core.deferred_speech_answer
    finally:
        await core.stop()
        await core.executor.close()
