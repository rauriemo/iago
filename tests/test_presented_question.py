"""Actual controller/speech lifecycle with a synthetic voice and sink acknowledgment."""

import pytest

from reachy_brain.behavior.thumbs import Observation, Question
from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


class Voice:
    async def stream(self, text):
        yield bytes(1920)


@pytest.mark.features("P10", "C8")
@pytest.mark.scenario("QUESTION-AUDIBLE-BINDING")
async def test_question_activates_only_after_its_heard_segment():
    events = []

    async def send(message):
        events.append(message)

    core = Conversation(
        Settings(_env_file=None),
        None,
        {"openai": Voice()},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    camera = core.visual.source(core.connection, "camera", "Synthetic camera")
    core.selected_source = camera.id
    async with core.make_speech(core.epoch) as speech:
        core.speech = speech
        result = await core.ask_yes_no("Would you like to explore that idea?")
        assert core.thumbs.question is None
    assert core.thumbs.question is None

    marker = next(e for e in events if e["type"] == "question_segment")
    await core.heard(core.epoch, "unknown-segment")
    assert core.thumbs.question is None
    await core.heard(core.epoch, marker["segment"])
    assert core.thumbs.question.id == result["question"]
    assert core.thumbs.question.source == camera.id
    await core.heard(core.epoch, marker["segment"])
    assert len([e for e in events if e["type"] == "active_question"]) == 1
    await core.stop()
    await core.heard(0, marker["segment"])
    assert core.thumbs.question is None


@pytest.mark.features("P10", "C8")
@pytest.mark.scenario("QUESTION-GESTURE-SPEECH-SLOT")
async def test_delayed_speech_removes_gesture_from_same_history_slot():
    import time

    events = []

    async def send(message):
        events.append(message)

    core = Conversation(
        Settings(_env_file=None),
        None,
        {"openai": Voice()},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.thumbs.enabled = True
    camera = core.visual.source(core.connection, "camera", "Synthetic camera")
    at = time.time()
    core.thumbs.present(Question("q", core.session, 0, camera.id, camera.generation, at, at + 15))
    for offset, gesture in [
        (0, "neutral"),
        (0.31, "neutral"),
        (0.4, "thumb_up"),
        (0.76, "thumb_up"),
    ]:
        core.thumbs.observe(
            Observation(
                str(offset),
                camera.id,
                camera.generation,
                "camera",
                at + offset,
                gesture,
                0.95,
                1,
                True,
            ),
            now=at + offset,
        )
    response = core.thumbs.poll(now=at + 1.02)
    assert response
    await core.accept_thumb(response)
    assert len([m for m in core.history if m["role"] == "user"]) == 1
    await core.speech_onset(at + 0.4)
    assert len([m for m in core.history if m["role"] == "user"]) == 0
    assert [e["slot"] for e in events if e["type"] == "gesture_superseded"] == [response["slot"]]
    await core.stop()
