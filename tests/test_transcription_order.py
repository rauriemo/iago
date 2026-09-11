"""Actual adapter ordering against an injected socket; no provider calls."""

import asyncio
import json

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.live import ProviderError, Transcription


@pytest.mark.features("C1", "C2", "D2", "D3")
@pytest.mark.scenario("TRANSCRIPTION-APPEND-COMMIT-ORDER")
async def test_concurrent_finish_waits_for_append_and_coalesces_empty_commit():
    entered, release = asyncio.Event(), asyncio.Event()
    messages = []

    class Socket:
        async def send(self, raw):
            event = json.loads(raw)
            if event["type"] == "input_audio_buffer.append":
                entered.set()
                await release.wait()
            messages.append(event["type"])

    stt = Transcription(Settings(_env_file=None), None)
    stt.ws = Socket()
    assert not await stt.commit()
    append = asyncio.create_task(stt.append(bytes(9600)))
    await entered.wait()
    first = asyncio.create_task(stt.commit())
    second = asyncio.create_task(stt.commit())
    await asyncio.sleep(0)
    assert not messages
    release.set()
    await append
    assert await first and not await second
    assert messages == ["input_audio_buffer.append", "input_audio_buffer.commit"]
    assert stt.buffered_bytes == 0


@pytest.mark.features("C1", "C2", "D5")
@pytest.mark.scenario("TRANSCRIPTION-UNCERTAIN-SEND")
async def test_canceled_send_retires_transport_without_replay():
    entered = asyncio.Event()

    class Socket:
        closed = False
        sends = 0

        async def send(self, raw):
            self.sends += 1
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    stt = Transcription(Settings(_env_file=None), None)
    socket = stt.ws = Socket()
    task = asyncio.create_task(stt.append(bytes(960)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert socket.closed and stt.input_failed
    with pytest.raises(ProviderError):
        await stt.append(bytes(960))
    assert socket.sends == 1
