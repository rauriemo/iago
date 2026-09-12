"""No network: live validation must share saved application admission state."""

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.live import ProviderError, ProviderGate
from reachy_brain.providers.usage_storage import UsageStorage
from reachy_brain.providers.validation import validation_gate


@pytest.mark.features("D6")
@pytest.mark.scenario("LIVE-VALIDATION-SHARED-ACCOUNTING")
@pytest.mark.parametrize("provider", ["openai", "elevenlabs"])
async def test_live_validation_restores_and_saves_application_limits(tmp_path, provider):
    settings = Settings(_env_file=None, data_dir=tmp_path, iago_development_budget="unlimited")
    original = ProviderGate(settings)
    storage = UsageStorage(original, tmp_path / "usage-accounting.json")
    await storage.start()
    original.limit(provider)
    original.record("astra", "synthetic-old-request", {}, model=settings.brain_model)
    await storage.close()
    other = "elevenlabs" if provider == "openai" else "openai"
    async with validation_gate(settings) as gate:
        with pytest.raises(ProviderError, match="plan_limit"):
            await gate.checkpoint(provider)
        gate.require(other)
        assert gate.seen_usage == original.seen_usage
        assert gate.unknown_charges == 1
        gate.limit(other)
    async with validation_gate(settings) as recovered:
        assert recovered.limited == {provider, other}
        assert recovered.seen_usage == original.seen_usage


@pytest.mark.features("D6")
@pytest.mark.scenario("LIVE-VALIDATION-ACCOUNTING-FAIL-CLOSED")
@pytest.mark.parametrize("condition", ["owned", "invalid", "budget"])
async def test_live_validation_cannot_bypass_accounting(tmp_path, condition):
    settings = Settings(_env_file=None, data_dir=tmp_path, iago_development_budget=1)
    path = tmp_path / "usage-accounting.json"
    if condition == "invalid":
        path.write_text("invalid checkpoint", encoding="utf-8")
        with pytest.raises(RuntimeError, match="usage_storage_invalid"):
            async with validation_gate(settings):
                pytest.fail("Invalid saved accounting admitted validation")
    else:
        async with validation_gate(settings) as gate:
            if condition == "owned":
                with pytest.raises(RuntimeError, match="usage_storage_lock_unavailable"):
                    async with validation_gate(settings):
                        pytest.fail("Concurrent accounting owner admitted validation")
            else:
                gate.estimated_usd = 1
                gate.persistence.changed()
        # Failed ownership did not damage the first owner's checkpoint/lock.
        async with validation_gate(settings) as gate:
            if condition == "budget":
                with pytest.raises(ProviderError, match="budget"):
                    await gate.checkpoint("openai")
            else:
                await gate.checkpoint("openai")
