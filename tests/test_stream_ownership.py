"""Retained generator references prove closure without relying on garbage collection."""

import asyncio

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.core.ownership import HeardLedger
from reachy_brain.core.speech import SpeechStream
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C2", "C4", "C8")
@pytest.mark.scenario("ASTRA-INVALIDATED-ITERATOR-CLOSURE")
async def test_invalidated_turn_closes_retained_model_iterator_before_return():
    closed = []
    retained = []
    messages = []

    class Brain:
        def stream(self, history, tools):
            async def events():
                try:
                    await core.stop()
                    yield {"type": "text", "text": "Late text must not be shown."}
                    pytest.fail("invalidated model stream consumed further")
                finally:
                    closed.append(True)

            source = events()
            retained.append(source)
            return source

    async def send(message):
        messages.append(message)

    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    await core.answer(0)
    assert closed == [True] and retained[0].ag_frame is None
    assert not any(m["type"] in {"answer_partial", "answer", "audio"} for m in messages)


@pytest.mark.features("C2", "C5", "C6", "D6")
@pytest.mark.scenario("SPEECH-PRIMARY-CLOSED-BEFORE-FALLBACK")
async def test_primary_iterator_is_closed_before_fallback_opens():
    order = []
    retained = []

    class Primary:
        def stream(self, text):
            async def chunks():
                try:
                    order.append("primary_open")
                    yield b"x"  # Malformed PCM before any playable bytes leave the host.
                finally:
                    order.append("primary_closed")

            source = chunks()
            retained.append(source)
            return source

    class Fallback:
        async def stream(self, text):
            assert order == ["primary_open", "primary_closed"]
            order.append("fallback_open")
            yield bytes(960)

    async def emit(kind, **data):
        pass

    async with SpeechStream(
        0,
        lambda: True,
        emit,
        {"elevenlabs": Primary(), "openai": Fallback()},
        "elevenlabs",
        HeardLedger(),
        0,
    ) as speech:
        await speech.feed("Synthetic fallback.")
    assert order == ["primary_open", "primary_closed", "fallback_open"]
    assert retained[0].ag_frame is None


@pytest.mark.features("C2", "C5", "C6", "D6")
@pytest.mark.scenario("SPEECH-CONSUMER-ERROR-ITERATOR-CLOSURE")
@pytest.mark.parametrize("failure", ["pcm", "sink", "cancel"])
async def test_consumer_error_closes_suspended_synthesis_iterator(failure):
    closed, retained = [], []
    valid = [True]

    class Voice:
        def stream(self, text):
            async def chunks():
                try:
                    if failure == "cancel":
                        valid[0] = False
                    yield b"x" if failure == "pcm" else bytes(960)
                    pytest.fail("consumer error must stop synthesis consumption")
                finally:
                    closed.append(True)

            source = chunks()
            retained.append(source)
            return source

    async def emit(kind, **data):
        if kind == "audio" and failure == "sink":
            raise OSError("synthetic sink loss")

    error = {"pcm": ValueError, "sink": OSError, "cancel": asyncio.CancelledError}[failure]
    with pytest.raises(error):
        async with SpeechStream(
            0, lambda: valid[0], emit, {"openai": Voice()}, "openai", HeardLedger(), 0
        ) as speech:
            await speech.feed("Synthetic speech.")
    assert closed == [True] and retained[0].ag_frame is None
