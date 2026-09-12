"""Synthetic recognition events exercise real onset/commit/reference ordering."""

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore
from reachy_brain.web.app import create_app


def prepared_image():
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(data, format="PNG")
    return VisualStore.prepare(data.getvalue())


def controller():
    events, turns = [], []

    async def send(event):
        events.append(event)

    visual = VisualStore()
    source = visual.source("synthetic", "screen", "Screen")
    frames = [visual.add(source.id, 0, time.time() - 20 + i, prepared_image()) for i in range(2)]
    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        visual,
        send,
    )
    core.mode = "conversation"
    original = core.user_turn

    async def turn(text, **kwargs):
        turns.append((text, kwargs.get("frame_id"), kwargs.get("region")))
        await original(text, **kwargs)

    async def answer(epoch):
        pass

    core.user_turn, core.answer = turn, answer
    return core, frames, turns, events


class STT:
    async def commit(self):
        return True


@pytest.mark.features("V4", "V5", "D4")
@pytest.mark.scenario("SPOKEN-REFERENCE-WEBSOCKET-ACK")
def test_reference_acknowledgment_and_onset_use_same_server_token(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    visual = app.state.visual
    source = visual.source("synthetic", "screen", "Screen")
    frame = visual.add(source.id, 0, time.time(), prepared_image())
    with TestClient(app) as client:
        with client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as ws:
            ws.send_text("test")
            assert ws.receive_json()["type"] == "ready"
            core = app.state.active["conversation"]
            core.mode = "conversation"  # Synthetic runtime setup; no provider or microphone.
            ws.send_json(dict(type="speech_reference", frame=frame.id, request="synthetic-request"))
            armed = ws.receive_json()
            assert armed["status"] == "armed" and armed["request"] == "synthetic-request"
            assert armed["frame"]["id"] == frame.id
            ws.send_json(dict(type="speech_start", captured=time.time(), clock_uncertainty=0))
            assert ws.receive_json()["type"] == "stop"
            bound = ws.receive_json()
            assert bound["status"] == "bound" and bound["token"] == armed["token"]
            assert core.pending_visual_reference is None
            ws.send_json(dict(type="speech_reference", frame=None, request="cancel-next"))
            assert ws.receive_json()["status"] == "cleared"
            assert core.input_visual_reference["frame_id"] == frame.id


async def acknowledged(core, item):
    await core.transcription_event(dict(type="input_audio_buffer.committed", item_id=item))


async def completed(core, item, text):
    await core.transcription_event(
        dict(
            type="conversation.item.input_audio_transcription.completed",
            item_id=item,
            transcript=text,
        )
    )


@pytest.mark.features("V4", "V5", "C1", "D5")
@pytest.mark.scenario("SPOKEN-REFERENCE-ONSET-AND-REORDER")
async def test_reference_freezes_at_onset_and_follows_reordered_transcripts():
    core, frames, turns, _ = controller()
    try:
        core.select_speech_reference(frames[0].id)
        await core.speech_onset(time.time())
        core.select_speech_reference(frames[1].id, [1, 2, 5, 6])
        await core.commit_recognition(STT())
        await core.speech_onset(time.time())
        await core.commit_recognition(STT())
        await acknowledged(core, "first")
        await acknowledged(core, "second")
        await completed(core, "second", "Later speech")
        assert not turns
        await completed(core, "first", "Earlier speech")
        await core.task
        assert turns == [
            ("Earlier speech", frames[0].id, None),
            ("Later speech", frames[1].id, (1, 2, 5, 6)),
        ]
        await completed(core, "first", "Earlier speech")
        assert len(turns) == 2
        assert not core.reference_commits and not core.reference_inputs
    finally:
        await core.stop()
        await core.executor.close()


@pytest.mark.features("V4", "V5", "C1", "D5")
@pytest.mark.scenario("SPOKEN-REFERENCE-RETIRED-IMAGE")
async def test_clear_before_final_text_never_substitutes_or_closes_recognition():
    core, frames, turns, events = controller()
    try:
        core.select_speech_reference(frames[0].id)
        await core.speech_onset(time.time())
        await core.commit_recognition(STT())
        await acknowledged(core, "first")
        await core.clear_evidence()
        await completed(core, "first", "About the selected image")
        assert core.mode == "conversation" and not core.history
        assert not turns
        assert any(e["type"] == "error" and "reference" in e["message"] for e in events)
        assert not core.reference_inputs
    finally:
        await core.stop()
        await core.executor.close()


@pytest.mark.features("V4", "C1", "D5")
@pytest.mark.scenario("SPOKEN-REFERENCE-EMPTY-COMMIT-AND-MODE")
async def test_rejected_commit_does_not_reuse_consumed_reference():
    core, frames, turns, _ = controller()

    class Empty:
        async def commit(self):
            return False

    try:
        core.select_speech_reference(frames[0].id)
        await core.speech_onset(time.time())
        assert not await core.commit_recognition(Empty())
        assert not core.reference_commits
        await core.speech_onset(time.time())
        await core.commit_recognition(STT())
        await acknowledged(core, "ordinary")
        await completed(core, "ordinary", "No selected reference")
        await core.task
        assert turns == [("No selected reference", None, None)]
        core.select_speech_reference(frames[1].id)
        await core.set_mode("aware")
        assert core.pending_visual_reference is None and core.input_visual_reference is None
    finally:
        await core.stop()
        await core.executor.close()


@pytest.mark.features("V4", "V5", "C1", "C3")
@pytest.mark.scenario("SPOKEN-REFERENCE-MISSING-ONSET")
async def test_manual_commit_without_onset_does_not_guess_reference():
    core, frames, turns, events = controller()
    try:
        core.select_speech_reference(frames[0].id)
        await core.commit_recognition(STT())
        await acknowledged(core, "manual")
        await completed(core, "manual", "Speech without a known onset")
        assert not turns and not core.history
        assert core.pending_visual_reference["frame_id"] == frames[0].id
        assert any(e["type"] == "error" and "speech start" in e["message"] for e in events)
        await core.speech_onset(time.time())
        await core.commit_recognition(STT())
        await acknowledged(core, "retry")
        await completed(core, "retry", "Repeat after onset")
        await core.task
        assert turns == [("Repeat after onset", frames[0].id, None)]
    finally:
        await core.stop()
        await core.executor.close()
