"""Real checkpoint barriers with synthetic SDK calls; no billable request is sent."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.live import (
    AstraBrain,
    ElevenSpeech,
    OpenAISpeech,
    ProviderError,
    ProviderGate,
    Transcription,
)
from reachy_brain.providers.usage_storage import UsageStorage


@pytest.mark.features("C2", "D6")
@pytest.mark.scenario("PROVIDER-DISPATCH-CHECKPOINT-FENCE")
@pytest.mark.parametrize("outcome", ["dispatch", "cancel", "disk_failure", "limit"])
@pytest.mark.parametrize(
    "adapter_kind", ["astra", "openai_speech", "eleven_speech", "transcription"]
)
async def test_adapter_waits_for_checkpoint_before_dispatch(
    tmp_path, monkeypatch, outcome, adapter_kind
):
    create = (AsyncMock if adapter_kind == "astra" else Mock)(
        side_effect=OSError("synthetic dispatch boundary")
    )
    monkeypatch.setattr(
        "reachy_brain.providers.live.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            responses=SimpleNamespace(create=create),
            close=AsyncMock(),
            audio=SimpleNamespace(
                speech=SimpleNamespace(with_streaming_response=SimpleNamespace(create=create))
            ),
        ),
    )
    monkeypatch.setattr("reachy_brain.providers.live.connect", create)
    settings = Settings(
        _env_file=None, openai_api_key="synthetic", iago_development_budget="unlimited"
    )
    gate = ProviderGate(settings)
    storage = UsageStorage(gate, tmp_path / "usage.json")
    entered, release = threading.Event(), threading.Event()
    original = storage.write

    def write(checkpoint):
        entered.set()
        assert release.wait(5)
        if outcome == "disk_failure":
            raise OSError("synthetic disk failure")
        original(checkpoint)

    monkeypatch.setattr(storage, "write", write)
    await storage.start()
    adapter = {
        "astra": AstraBrain,
        "openai_speech": OpenAISpeech,
        "eleven_speech": ElevenSpeech,
        "transcription": Transcription,
    }[adapter_kind](settings, gate)
    stream = None
    if adapter_kind == "astra":
        stream = adapter.stream([], [])
    elif adapter_kind == "openai_speech":
        stream = adapter.stream("Synthetic speech.")
    elif adapter_kind == "eleven_speech":
        stream = adapter._stream_pcm("Synthetic speech.")
    pending = asyncio.create_task(anext(stream) if stream is not None else adapter.start())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert create.call_count == 0 and not pending.done()
        if outcome == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 0.5)
            assert create.call_count == 0
        if outcome == "limit":
            gate.limit("elevenlabs" if adapter_kind == "eleven_speech" else "openai")
        release.set()
        if outcome == "dispatch":
            with pytest.raises(OSError, match="synthetic dispatch boundary"):
                await pending
            assert create.call_count == 1
        elif outcome == "disk_failure":
            with pytest.raises(ProviderError, match="usage_persistence_unavailable"):
                await pending
            assert create.call_count == 0
        elif outcome == "limit":
            with pytest.raises(ProviderError, match="plan_limit"):
                await pending
            assert create.call_count == 0
        assert len(gate.usage) == 1
        usage = gate.usage[0]
        assert usage["usage"]["dispatched"] is (outcome == "dispatch")
        assert gate.unknown_charges == (1 if outcome == "dispatch" else 0)
        if outcome != "dispatch":
            assert usage["estimated_usd"] == 0 and usage["usage_priced_completely"]
    finally:
        release.set()
        if stream is not None:
            await stream.aclose()
        if hasattr(adapter, "close"):
            await adapter.close()
        if outcome == "disk_failure":
            with pytest.raises(RuntimeError, match="usage_persistence_failed"):
                await storage.close()
        else:
            await storage.close()
