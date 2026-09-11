"""Synthetic streams test setup policy; actual PCM access uses the live-provider suite."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.live import ElevenSpeech, ProviderGate


def voice():
    settings = Settings(
        _env_file=None,
        elevenlabs_api_key="synthetic-secret",
        elevenlabs_voice_id="synthetic-voice",
        iago_development_budget="unlimited",
    )
    result = ElevenSpeech(settings, ProviderGate(settings))
    result.validate_metadata = AsyncMock(return_value={"valid": True})
    return result


@pytest.mark.features("C5", "C6", "D4")
@pytest.mark.scenario("VOICE-FORMAT-VALIDATION-CACHE")
async def test_concurrent_setup_probes_once_and_configuration_invalidates_cache():
    adapter = voice()
    calls = []

    async def stream(text):
        calls.append(text)
        await asyncio.sleep(0)
        yield bytes(9600)

    adapter.stream = stream
    first, second = await asyncio.gather(adapter.validate(), adapter.validate())
    assert first["valid"] and second["valid"]
    assert {first["cached"], second["cached"]} == {False, True}
    assert len(calls) == 1
    adapter.settings.elevenlabs_voice_id = "another-exact-voice"
    assert (await adapter.validate())["valid"]
    assert len(calls) == 2
    adapter.validated = (adapter.validated[0], 0)
    assert (await adapter.validate())["valid"]
    assert len(calls) == 3
    adapter.gate.limit("elevenlabs")
    assert await adapter.validate() == {"valid": False, "reason": "plan_limit"}
    assert len(calls) == 3


@pytest.mark.features("C5", "C6", "D4")
@pytest.mark.scenario("VOICE-FORMAT-INVALID-STREAM")
@pytest.mark.parametrize("outcome", ["empty", "odd", "oversize", "timeout", "canceled"])
async def test_metadata_alone_cannot_select_voice_and_probe_closes(outcome):
    adapter = voice()
    closed = []

    async def stream(text):
        try:
            if outcome == "timeout":
                raise TimeoutError("synthetic-secret must not escape")
            if outcome == "canceled":
                raise asyncio.CancelledError()
            yield {"empty": b"", "odd": bytes(3), "oversize": bytes(240002)}[outcome]
        finally:
            closed.append(True)

    adapter.stream = stream
    if outcome == "canceled":
        with pytest.raises(asyncio.CancelledError):
            await adapter.validate()
    else:
        result = await adapter.validate()
        assert not result["valid"] and "synthetic-secret" not in str(result)
    assert adapter.validated is None and not adapter.validation_lock.locked()
    assert closed == [True]


@pytest.mark.features("C5", "C6", "D4")
@pytest.mark.scenario("VOICE-FORMAT-METADATA-PREREQUISITE")
async def test_invalid_metadata_makes_no_synthesis_attempt():
    adapter = voice()
    adapter.validate_metadata.return_value = {"valid": False, "reason": "missing_key_or_voice"}
    adapter.stream = AsyncMock(side_effect=AssertionError("must not synthesize"))
    assert await adapter.validate() == {"valid": False, "reason": "missing_key_or_voice"}
    adapter.stream.assert_not_called()


@pytest.mark.features("C5", "C6", "D4")
@pytest.mark.scenario("VOICE-FORMAT-CACHE-STREAM-FAILURE")
async def test_stream_failure_invalidates_cache_but_user_stop_does_not():
    adapter = voice()
    adapter.validated = ("synthetic-identity", float("inf"))
    closed = []

    async def broken(text):
        try:
            yield bytes(960)
            raise OSError("synthetic transport failure")
        finally:
            closed.append(True)

    adapter._stream_pcm = broken
    stream = adapter.stream("synthetic")
    assert await anext(stream) == bytes(960)
    await stream.aclose()
    assert closed == [True] and adapter.validated is not None
    stream = adapter.stream("synthetic")
    assert await anext(stream) == bytes(960)
    with pytest.raises(OSError):
        await anext(stream)
    assert adapter.validated is None and closed == [True, True]
