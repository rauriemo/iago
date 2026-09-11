"""Controlled clocks and synthetic transport failures; no provider availability claim."""

import asyncio
import base64
import json

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.circuit import CircuitOpen, SpeechCircuit
from reachy_brain.providers.live import ElevenSpeech, ProviderError, ProviderGate


@pytest.mark.features("C5", "C6", "D5")
@pytest.mark.scenario("SPEECH-CIRCUIT-RECOVERY-OWNERSHIP")
def test_threshold_single_recovery_probe_and_canceled_probe():
    now = [1.0]
    circuit = SpeechCircuit(clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(OSError), circuit.attempt():
            raise OSError("synthetic")
    assert circuit.status() == {"state": "open", "failures": 3, "retry_after_seconds": 30}
    with pytest.raises(CircuitOpen), circuit.attempt():
        pytest.fail("must not dispatch")
    now[0] += 30
    with pytest.raises(asyncio.CancelledError), circuit.attempt():
        with pytest.raises(CircuitOpen), circuit.attempt():
            pytest.fail("second recovery probe dispatched")
        raise asyncio.CancelledError()
    assert circuit.failures == 3 and not circuit.probing
    with circuit.attempt():
        pass
    assert circuit.status()["state"] == "closed" and circuit.failures == 0


@pytest.mark.features("C5", "C6", "D5")
@pytest.mark.scenario("SPEECH-CIRCUIT-LATE-COMPLETION")
def test_old_inflight_success_cannot_close_newly_opened_circuit():
    circuit = SpeechCircuit()
    old = circuit.attempt()
    old.__enter__()
    for _ in range(3):
        with pytest.raises(OSError), circuit.attempt():
            raise OSError("synthetic")
    old.__exit__(None, None, None)
    assert circuit.status()["state"] == "open"


@pytest.mark.features("C5", "C6", "D5", "D6")
@pytest.mark.scenario("SPEECH-CIRCUIT-REAL-ADAPTER")
async def test_adapter_blocks_requests_without_new_usage_then_recovers(monkeypatch):
    attempts = []
    fail = [True]

    class Socket:
        async def __aenter__(self):
            attempts.append(True)
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, text):
            pass

        async def events(self):
            if fail[0]:
                raise OSError("synthetic failure")
            yield json.dumps({"audio": base64.b64encode(bytes(960)).decode()})
            yield json.dumps({"isFinal": True})

        def __aiter__(self):
            return self.events()

    monkeypatch.setattr("reachy_brain.providers.live.connect", lambda *a, **kw: Socket())
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    voice = ElevenSpeech(settings, gate)
    now = [1.0]
    voice.circuit.clock = lambda: now[0]
    for _ in range(3):
        with pytest.raises(OSError):
            _ = [chunk async for chunk in voice.stream("Synthetic")]
    with pytest.raises(ProviderError, match="speech_circuit_open"):
        _ = [chunk async for chunk in voice.stream("Synthetic")]
    assert len(attempts) == len(gate.usage) == 3
    assert (await voice.validate())["reason"] == "speech_circuit_open"
    now[0] += 30
    fail[0] = False
    stream = voice.stream("Synthetic")
    assert await anext(stream) == bytes(960)
    await stream.aclose()
    assert voice.circuit.status()["state"] == "open"
    assert [chunk async for chunk in voice.stream("Synthetic")] == [bytes(960)]
    assert voice.circuit.status()["state"] == "closed"
    assert len(attempts) == len(gate.usage) == 5
