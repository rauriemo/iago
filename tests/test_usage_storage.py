"""Real private checkpoints with synthetic usage and blocked-disk barriers."""

import asyncio
import json
import threading

import httpx
import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.providers.live import ProviderError, ProviderGate
from reachy_brain.providers.usage_storage import Checkpoint, UsageStorage
from reachy_brain.vision.store import VisualStore
from reachy_brain.web.app import create_app


@pytest.mark.features("D6")
@pytest.mark.scenario("USAGE-CHECKPOINT-RESTART")
async def test_restart_preserves_cost_dedupe_and_numeric_admission(tmp_path):
    settings = Settings(_env_file=None, iago_development_budget=0.001)
    gate = ProviderGate(settings)
    path = tmp_path / "usage.json"
    storage = UsageStorage(gate, path)
    await storage.start()
    usage = {"input_tokens": 100, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 0}}
    gate.record("astra", "response-test", usage, model="gpt-6-astra")
    gate.record("openai_tts", "speech-test", {"input_characters": 50})
    await storage.close()
    saved = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    assert "usage" not in saved and saved["unknown_charges"] == 1
    assert saved["estimated_usd"] == pytest.approx(0.0015)
    replacement = ProviderGate(settings)
    reopened = UsageStorage(replacement, path)
    await reopened.start()
    try:
        replacement.record("astra", "response-test", usage, model="gpt-6-astra")
        assert replacement.usage == [] and replacement.unknown_charges == 1
        assert replacement.estimated_usd == gate.estimated_usd
        with pytest.raises(ProviderError, match="development_budget"):
            replacement.require("openai")
        assert reopened.status()["status"] == "saved"
    finally:
        await reopened.close()


@pytest.mark.features("C2", "D6")
@pytest.mark.scenario("USAGE-DISK-BLOCK-STOP")
async def test_pending_disk_write_does_not_block_stop_or_lose_later_revision(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    storage = UsageStorage(gate, tmp_path / "usage.json")
    entered, release = threading.Event(), threading.Event()
    original = storage.write

    def blocked(value):
        entered.set()
        assert release.wait(5), "Synthetic disk barrier not released"
        original(value)

    monkeypatch.setattr(storage, "write", blocked)
    await storage.start()
    executor = ToolExecutor(ToolRegistry(), ActionPolicy(), None)
    emitted = []

    async def send(message):
        emitted.append(message)

    core = Conversation(settings, None, {}, executor, VisualStore(), send)
    core.mode = "conversation"
    try:
        gate.record("astra", "first", {})
        assert await asyncio.to_thread(entered.wait, 2)
        gate.record("astra", "second", {})
        await asyncio.wait_for(core.stop(), 0.5)
        assert core.epoch == 1
        assert any(m["type"] == "stop" for m in emitted)
        assert storage.status()["status"] == "pending"
    finally:
        release.set()
        await storage.close()
        await executor.close()
    saved = await asyncio.to_thread(storage.load)
    assert saved.revision == 2 and saved.unknown_charges == 2
    assert set(saved.identities) == {("astra", "first"), ("astra", "second")}


@pytest.mark.features("D6")
@pytest.mark.scenario("USAGE-STORAGE-FAILURE")
async def test_write_failure_blocks_new_work_and_preserves_previous_checkpoint(
    tmp_path, monkeypatch
):
    settings = Settings(_env_file=None, iago_development_budget="unlimited")
    gate = ProviderGate(settings)
    storage = UsageStorage(gate, tmp_path / "usage.json")
    await asyncio.to_thread(storage.write, Checkpoint(estimated_usd=0.2))
    original = await asyncio.to_thread(storage.path.read_bytes)
    await storage.start()

    def fail(source, destination):
        raise OSError("Synthetic private disk detail")

    monkeypatch.setattr("reachy_brain.providers.usage_storage.os.replace", fail)
    gate.record("astra", "first", {})
    await asyncio.wait_for(asyncio.shield(storage.worker), 2)
    assert storage.status()["status"] == "usage_persistence_failed"
    with pytest.raises(ProviderError, match="usage_persistence_unavailable"):
        gate.require("openai")
    with pytest.raises(RuntimeError, match="usage_persistence_failed"):
        await storage.close()
    assert "private" not in json.dumps(storage.status())
    assert await asyncio.to_thread(storage.path.read_bytes) == original
    assert await asyncio.to_thread(lambda: list(tmp_path.glob(".usage-*"))) == []


@pytest.mark.features("D6")
@pytest.mark.scenario("USAGE-STORAGE-CORRUPTION")
async def test_invalid_checkpoint_is_not_silently_reset(tmp_path):
    path = tmp_path / "usage.json"
    await asyncio.to_thread(path.write_text, '{"estimated_usd": -1}', encoding="utf-8")
    storage = UsageStorage(ProviderGate(Settings(_env_file=None)), path)
    with pytest.raises(RuntimeError, match="usage_storage_invalid"):
        await storage.start()
    assert await asyncio.to_thread(path.read_text, encoding="utf-8") == '{"estimated_usd": -1}'


@pytest.mark.features("D6")
@pytest.mark.scenario("USAGE-APPLICATION-RESTART")
async def test_application_lifecycle_restores_public_totals(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    gates = []

    def make_gate(value):
        gates.append(ProviderGate(value))
        return gates[-1]

    monkeypatch.setattr("reachy_brain.web.app.ProviderGate", make_gate)
    app = create_app(settings, token="synthetic")
    async with app.router.lifespan_context(app):
        gates[-1].record("astra", "synthetic-response", {})
    app = create_app(settings, token="synthetic")
    async with app.router.lifespan_context(app):
        active_id = gates[-1].begin_active(
            "openai_stt", "gpt-live-transcribe", {"sent_pcm_bytes": 48000}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            assert (await client.get("/api/status")).status_code == 401
            response = await client.get(
                "/api/status", headers={"Authorization": "Bearer synthetic"}
            )
        assert response.status_code == 200
        assert response.json()["costs"]["unknown_charges"] == 1
        assert response.json()["costs"]["persistence"]["status"] == "saved"
        assert response.json()["usage"] == []
        active = response.json()["costs"]["active_attempts"]
        assert len(active) == 1 and active[0]["attempt_id"] == active_id
        assert active[0]["counters"]["sent_pcm_bytes"] == 48000
        gates[-1].end_active(active_id)
