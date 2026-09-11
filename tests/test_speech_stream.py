"""Deterministic synthesis flow control; synthetic PCM, no speaker timing claims."""

import asyncio

import pytest

from reachy_brain.core.ownership import HeardLedger
from reachy_brain.core.speech import SpeechStream


class Voice:
    def __init__(self, size=1920):
        self.size = size
        self.calls = []

    async def stream(self, text):
        self.calls.append(text)
        yield bytes(self.size)


@pytest.mark.features("C1", "C2", "C5", "C6", "C8")
@pytest.mark.scenario("SPEECH-STREAM-CREDIT")
async def test_sentence_starts_before_answer_finishes_and_credit_is_bounded():
    voice = Voice(12000)
    events = []

    async def emit(kind, **data):
        events.append({"type": kind, **data})

    stream = SpeechStream(
        7, lambda: True, emit, {"openai": voice}, "openai", HeardLedger(), 0, max_buffer_bytes=3840
    )
    async with stream:
        await stream.feed("First sentence. ")
        for _ in range(30):
            await asyncio.sleep(0)
            if any(e["type"] == "audio" for e in events):
                break
        assert voice.calls == ["First sentence."]
        assert stream.pending_bytes <= 3840
        assert stream.pending_bytes > 0
        stream.acknowledge(6, 0)
        assert stream.pending_bytes > 0

        async def consume():
            while not stream.closed:
                for event in list(events):
                    if event["type"] == "audio":
                        stream.acknowledge(7, event["sequence"])
                await asyncio.sleep(0)

        consumer = asyncio.create_task(consume())
        await stream.feed("Second sentence.")
    await consumer
    assert voice.calls == ["First sentence.", "Second sentence."]
    audio = [e for e in events if e["type"] == "audio"]
    assert [e["sequence"] for e in audio] == list(range(len(audio)))


@pytest.mark.features("C2", "C8")
@pytest.mark.scenario("SPEECH-CANCEL-CREDIT-WAIT")
async def test_cancel_while_sink_credit_is_exhausted():
    voice = Voice(48000)
    events = []

    async def emit(kind, **data):
        events.append(kind)

    stream = SpeechStream(
        1, lambda: True, emit, {"openai": voice}, "openai", HeardLedger(), 0, max_buffer_bytes=1920
    )

    async def run():
        async with stream:
            await stream.feed("A long synthetic sentence.")

    task = asyncio.create_task(run())
    for _ in range(30):
        await asyncio.sleep(0)
        if "audio" in events:
            break
    assert "audio" in events
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
    assert stream.worker.done()


@pytest.mark.features("C2", "C5", "C6")
@pytest.mark.scenario("SPEECH-PARTIAL-FAILURE")
@pytest.mark.parametrize("partial", [False, True])
async def test_fallback_only_before_first_dispatched_audio(partial):
    class FailingVoice:
        async def stream(self, text):
            if partial:
                yield bytes(1920)
            raise OSError("synthetic failure")

    fallback = Voice()
    events = []

    async def emit(kind, **data):
        events.append(kind)

    stream = SpeechStream(
        1,
        lambda: True,
        emit,
        {"openai": fallback, "elevenlabs": FailingVoice()},
        "elevenlabs",
        HeardLedger(),
        0,
    )

    async def run():
        async with stream:
            await stream.feed("A synthetic response.")

    if partial:
        with pytest.raises(OSError):
            await run()
        assert not fallback.calls
    else:
        await run()
        assert fallback.calls == ["A synthetic response."]
        assert "voice_fallback" in events
