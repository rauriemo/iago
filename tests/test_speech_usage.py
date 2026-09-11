"""Real adapter control flow with explicitly fake SDK/WebSocket audio and no accounts."""

import base64
import json
from types import SimpleNamespace

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.ownership import HeardLedger
from reachy_brain.core.speech import SpeechStream
from reachy_brain.providers.live import ElevenSpeech, OpenAISpeech, ProviderError, ProviderGate


@pytest.mark.features("C5", "C6", "D6")
@pytest.mark.scenario("SPEECH-ATTEMPT-USAGE-OUTCOME")
@pytest.mark.parametrize("provider", ["openai", "elevenlabs"])
@pytest.mark.parametrize("outcome", ["completed", "interrupted", "failed"])
async def test_speech_attempts_keep_partial_audio_without_text(monkeypatch, provider, outcome):
    class Response:
        headers = {"x-request-id": "synthetic-provider-request"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, message):
            pass

        async def iter_bytes(self, **kwargs):
            yield bytes(960)
            if outcome == "failed":
                raise RuntimeError("Synthetic transport failure")
            yield bytes(960)

        async def messages(self):
            async for chunk in self.iter_bytes():
                yield json.dumps({"audio": base64.b64encode(chunk).decode()})
            yield json.dumps({"isFinal": True})

        def __aiter__(self):
            return self.messages()

    async def close():
        pass

    monkeypatch.setattr(
        "reachy_brain.providers.live.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            audio=SimpleNamespace(
                speech=SimpleNamespace(
                    with_streaming_response=SimpleNamespace(create=lambda **kwargs: Response())
                )
            ),
            close=close,
        ),
    )
    monkeypatch.setattr("reachy_brain.providers.live.connect", lambda *args, **kwargs: Response())
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    voice = OpenAISpeech(settings, gate) if provider == "openai" else ElevenSpeech(settings, gate)
    text = "Private synthetic speech"
    stream = voice.stream(text)
    assert await anext(stream) == bytes(960)
    active = gate.active_usage()
    assert len(active) == 1
    assert active[0]["counters"]["received_pcm_bytes"] == 960
    assert active[0]["billing_complete"] is False
    assert text not in json.dumps(active)
    if outcome == "interrupted":
        await stream.aclose()
    elif outcome == "failed":
        with pytest.raises(RuntimeError, match="Synthetic transport failure"):
            await anext(stream)
    else:
        assert await anext(stream) == bytes(960)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert len(gate.usage) == 1
    assert gate.active_usage() == []
    row = gate.usage[0]
    assert row["usage"]["attempt_id"] == active[0]["attempt_id"]
    assert row["usage"]["status"] == outcome
    assert row["usage"]["received_pcm_bytes"] == (1920 if outcome == "completed" else 960)
    assert row["usage"]["received_audio_seconds"] == (0.04 if outcome == "completed" else 0.02)
    assert row["usage"]["input_characters"] == len(text)
    assert row["usage"]["billing_usage_known"] is False
    assert row["estimated_usd"] is None and gate.unknown_charges == 1
    assert row["usage"]["request_id_origin"] == (
        "provider" if provider == "openai" else "local_attempt"
    )
    assert text not in json.dumps(row)


@pytest.mark.features("C5", "C6", "D6")
@pytest.mark.scenario("ELEVEN-INCOMPLETE-STREAM")
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("final", [None, False, "true"])
async def test_incomplete_eleven_stream_keeps_failure_and_never_replays_audio(
    monkeypatch, partial, final
):
    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, message):
            pass

        async def messages(self):
            if partial:
                yield json.dumps({"audio": base64.b64encode(bytes(960)).decode()})
            yield json.dumps({"isFinal": final})

        def __aiter__(self):
            return self.messages()

    class Fallback:
        calls = 0

        async def stream(self, text):
            self.calls += 1
            yield bytes(1920)

    monkeypatch.setattr("reachy_brain.providers.live.connect", lambda *args, **kwargs: Socket())
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    fallback = Fallback()
    events = []

    async def emit(kind, **data):
        events.append(kind)

    async def run():
        async with SpeechStream(
            1,
            lambda: True,
            emit,
            {"elevenlabs": ElevenSpeech(settings, gate), "openai": fallback},
            "elevenlabs",
            HeardLedger(),
            0,
        ) as speech:
            await speech.feed("Synthetic response.")

    if partial:
        with pytest.raises(ProviderError, match="incomplete_stream"):
            await run()
        assert fallback.calls == 0
        assert "segment_end" not in events
    else:
        await run()
        assert fallback.calls == 1
        assert "voice_fallback" in events
    assert len(gate.usage) == 1
    assert gate.usage[0]["usage"]["status"] == "failed"
    assert gate.usage[0]["usage"]["received_pcm_bytes"] == (960 if partial else 0)
    assert gate.usage[0]["estimated_usd"] is None
