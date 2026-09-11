"""Real controller streams text to completion after synthetic speech failures."""

import asyncio

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C2", "C5", "C6", "C8")
@pytest.mark.scenario("SPEECH-FAILURE-TEXT-CONTINUES")
@pytest.mark.parametrize("partial,cancel", [(False, False), (True, False), (False, True)])
async def test_text_completion_does_not_replay_or_record_unheard_speech(partial, cancel):
    failed = asyncio.Event()
    messages = []
    calls = []

    class Brain:
        async def stream(self, history, tools):
            yield {"type": "text", "text": "First sentence. "}
            await asyncio.wait_for(failed.wait(), 2)
            yield {"type": "text", "text": "Remaining answer survives speech failure."}

    class Voice:
        def __init__(self, name):
            self.name = name

        async def stream(self, text):
            calls.append(self.name)
            if partial:
                yield bytes(960)
            raise OSError("synthetic speech failure")

    async def send(event):
        messages.append(event)
        if event["type"] == "speech_error":
            failed.set()
            if cancel:
                await core.stop()

    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {"elevenlabs": Voice("elevenlabs"), "openai": Voice("openai")},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.voice_provider = "elevenlabs"
    original = core.epoch
    await core.answer(original)
    answers = [e for e in messages if e["type"] == "answer"]
    if cancel:
        assert not answers
    else:
        assert answers[-1]["text"] == "First sentence. Remaining answer survives speech failure."
        assert core.answer_complete and core.answer_interrupted
    assert calls == (["elevenlabs"] if partial else ["elevenlabs", "openai"])
    assert len([e for e in messages if e["type"] == "speech_error"]) == 1
    error_index = next(i for i, e in enumerate(messages) if e["type"] == "speech_error")
    assert not any(e["type"] == "audio" for e in messages[error_index:])
    await core.stop()
    assert not core.history and core.speech is None
