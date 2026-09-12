"""Delayed synthetic recognition must not move current-scene evidence forward in time."""

import time

import pytest
from test_spoken_reference import STT, controller, prepared_image


@pytest.mark.features("V2", "V4", "V5", "V9", "C1")
@pytest.mark.scenario("SPEECH-CURRENT-FRAME-TIMING")
@pytest.mark.parametrize(
    "case", ["delayed", "uncertain", "clear", "missing", "upload", "clear_upload"]
)
async def test_current_scene_uses_utterance_time(case):
    core, old, turns, events = controller()
    source = core.visual.sources[old[0].source]
    core.selected_source = source.id
    if "upload" in case:
        source.kind = "upload"
    at = time.time() - 10
    expected = core.visual.add(
        source.id, 0, at - 4 if case == "uncertain" else at, prepared_image()
    )
    seen = []

    class Brain:
        async def stream(self, messages, tools):
            seen.extend(messages)
            if False:
                yield None

    class Voice:
        async def stream(self, text):
            if False:
                yield b""

    from reachy_brain.core.conversation import Conversation

    core.brain = Brain()
    core.voices = {"openai": Voice()}
    core.answer = Conversation.answer.__get__(core)
    await core.speech_onset(at)
    await core.commit_recognition(
        STT(), capture_end=at + 0.5, capture_clock_uncertainty=3 if case == "uncertain" else None
    )
    if case.startswith("clear"):
        core.visual.clear(source.id)
    elif case == "missing":
        del core.visual.frames[expected.id]
    latest = core.visual.add(source.id, source.generation, time.time(), prepared_image())
    await core.transcription_event({"type": "input_audio_buffer.committed", "item_id": "speech"})
    await core.transcription_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "speech",
            "transcript": "What am I showing you?",
        }
    )
    await core.task
    ids = {
        item.get("id")
        for message in events
        if message["type"] == "evidence"
        for item in message["frames"]
    }
    if case == "upload":
        assert ids == {latest.id}
        return
    assert latest.id not in ids
    if case in {"delayed", "uncertain"}:
        assert ids == {expected.id}
        if case == "uncertain":
            assert "temporally ambiguous" in str(seen)
    else:
        assert not ids
        assert "No retained frame near the spoken utterance" in str(seen)
