"""Actual controller events with synthetic inputs; no acoustic echo claim."""

import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.core.speech_activity import SpeechActivity
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.robot.session import RobotSession
from reachy_brain.vision.store import VisualStore
from reachy_brain.web.app import create_app


@pytest.mark.features("C1", "C2", "D6")
@pytest.mark.scenario("SPEECH-ACTIVITY-ONSET-AND-ACCEPTANCE")
async def test_onsets_without_transcripts_are_visible_and_no_text_is_retained():
    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        AsyncMock(),
    )
    core.answer = AsyncMock()
    core.mode = "conversation"
    await core.speech_onset(100)
    await core.speech_onset(101)
    assert core.speech_activity.snapshot()["counts"] == {
        "onset": 2,
        "uncertain_onset": 0,
        "accepted_speech": 0,
    }
    recognition = AsyncMock()
    recognition.commit.return_value = True
    await core.commit_recognition(recognition, capture_start=101.0, capture_end=102.0)
    await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": "input-1"})
    await core.transcription_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input-1",
            "transcript": "private words",
        }
    )
    await core.task
    await core.user_turn("typed words")
    await core.task
    await core.user_turn("  ", kind="speech")
    result = core.speech_activity.snapshot()
    assert result["counts"] == {"onset": 2, "uncertain_onset": 0, "accepted_speech": 1}
    assert [s["kind"] for s in result["samples"]] == ["onset", "onset", "accepted_speech"]
    assert "private words" not in json.dumps(result)
    assert "typed words" not in json.dumps(result)


@pytest.mark.features("C2", "D6")
@pytest.mark.scenario("SPEECH-ACTIVITY-BOUNDS")
def test_bounded_samples_keep_totals_and_owner_identity():
    now = [0.0]
    activity = SpeechActivity(clock=lambda: now[0])
    for i in range(520):
        now[0] += 1
        activity.record("onset", i)
    result = activity.snapshot()
    assert result["counts"]["onset"] == 520 and len(result["samples"]) == 512
    assert result["dropped_samples"] == 8
    assert result["samples"][0]["sequence"] == 9 and result["elapsed_seconds"] == 520
    result["counts"]["onset"] = -1
    assert activity.snapshot()["counts"]["onset"] == 520
    assert activity.snapshot()["owner"] != SpeechActivity().snapshot()["owner"]


@pytest.mark.features("D6", "C2")
@pytest.mark.scenario("SPEECH-ACTIVITY-PRIVATE-EXPORT")
def test_authenticated_export_and_owner_availability(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    with TestClient(app) as client:
        url = "/api/speech-activity"
        headers = {"Authorization": "Bearer test"}
        assert client.get(url).status_code == 401
        assert client.get(url, headers=headers).json() == {"available": False}
        with client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as control:
            control.send_text("test")
            assert control.receive_json()["type"] == "ready"
            activity = app.state.active["conversation"].speech_activity
            activity.record("onset", 1)
            result = client.get(url, headers=headers).json()
            assert result["available"] and result["counts"]["onset"] == 1
            assert result["owner"] == activity.owner


@pytest.mark.features("C2", "D2", "D3", "D6")
@pytest.mark.scenario("ROBOT-UNCERTAIN-ONSET-ACCOUNTING")
@pytest.mark.parametrize(
    "mapping",
    [
        None,
        {"time": 100, "stale": True, "uncertainty": 0.01},
        {"time": 100, "stale": False, "uncertainty": 0.2},
    ],
)
async def test_uncertain_edge_onsets_count_once_without_claiming_mapped_time(mapping):
    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        AsyncMock(),
    )
    activity = core.speech_activity
    core.speech_onset = AsyncMock()
    core.thumbs.invalidate = Mock(wraps=core.thumbs.invalidate)
    session = SimpleNamespace(
        core=core,
        speech_event_ids=deque(maxlen=128),
        edge=SimpleNamespace(clock=SimpleNamespace(local=lambda captured, now: mapping)),
    )
    event = {"type": "speech_start", "sequence": 10, "captured": 100}
    await RobotSession.event(session, event)
    await RobotSession.event(session, event, from_audio=True)
    assert activity.snapshot()["counts"] == {"onset": 0, "uncertain_onset": 1, "accepted_speech": 0}
    assert (
        sum(
            call.args == ("uncertain_speech_timing",)
            for call in core.thumbs.invalidate.call_args_list
        )
        == 1
    )
    assert core.input_capture_active and core.input_capture_start is None
    assert core.user_speaking
    core.speech_onset.assert_not_awaited()
    await core.executor.close()
