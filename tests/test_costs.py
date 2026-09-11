"""Synthetic token usage and configuration; no billable requests."""

from decimal import Decimal

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.costs import RateTable
from reachy_brain.providers.live import ProviderError, ProviderGate


@pytest.mark.features("D6")
@pytest.mark.scenario("COST-TOKEN-CACHE-LONG-CONTEXT")
def test_token_estimates_do_not_double_charge_reasoning_or_cache():
    rates = RateTable.load()
    usage = {
        "input_tokens": 100,
        "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 15},
        "output_tokens": 10,
        "output_tokens_details": {"reasoning_tokens": 8},
    }
    assert rates.estimate("gpt-6-astra", usage) == Decimal("0.0013575")
    assert rates.estimate(
        "gpt-6-astra",
        {
            "input_tokens": 300000,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 100,
        },
    ) == Decimal("6.0075")
    assert rates.estimate("other-model", usage) is None
    assert rates.estimate("gpt-6-astra", usage, service_tier="priority") is None
    assert rates.estimate("gpt-6-astra", {**usage, "input_tokens": 1}) is None
    assert rates.estimate("gpt-6-astra", {**usage, "output_tokens": True}) is None
    assert rates.estimate("gpt-6-astra", {}) is None


@pytest.mark.features("D6")
@pytest.mark.scenario("COST-GATE-DEDUPE-UNKNOWN")
def test_gate_estimates_dedupe_and_preserve_unknown_costs():
    gate = ProviderGate(Settings(_env_file=None, iago_development_budget=0.001))
    usage = {
        "input_tokens": 100,
        "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 15},
        "output_tokens": 10,
    }
    gate.record("astra", "request", usage, model="gpt-6-astra")
    gate.record("astra", "request", usage, model="gpt-6-astra")
    assert len(gate.usage) == 1
    assert gate.estimated_usd == pytest.approx(0.0013575)
    with pytest.raises(ProviderError, match="development_budget"):
        gate.require("openai")
    gate.record("openai_tts", "request", {"input_characters": 200})
    assert gate.usage[-1]["estimated_usd"] is None
    assert gate.unknown_charges == 1
    assert gate.estimated_usd == pytest.approx(0.0013575)
    for index in range(501):
        gate.record("astra", str(index), {}, model="gpt-6-astra")
    total = gate.estimated_usd
    gate.record("astra", "request", usage, model="gpt-6-astra")
    assert len(gate.usage) == 500 and gate.estimated_usd == total


@pytest.mark.features("D6")
@pytest.mark.scenario("ACTIVE-USAGE-CAPACITY")
def test_active_usage_is_bounded_and_snapshots_cannot_mutate_counters():
    gate = ProviderGate(Settings(_env_file=None))
    counters = {"received_pcm_bytes": 960, "text": "synthetic-private-text"}
    identities = [gate.begin_active("synthetic", "synthetic", counters) for _ in range(32)]
    with pytest.raises(ProviderError, match="active_usage_capacity"):
        gate.begin_active("synthetic", "synthetic", counters)
    rows = gate.active_usage()
    assert len(rows) == 32 and "text" not in rows[0]["counters"]
    rows[0]["counters"]["received_pcm_bytes"] = 123
    assert counters["received_pcm_bytes"] == 960
    for identity in identities:
        gate.end_active(identity)
    assert gate.active_usage() == []


@pytest.mark.features("D6")
@pytest.mark.scenario("COST-RETURNED-TRANSCRIPTION-DURATION")
def test_duration_prices_only_returned_units_and_keeps_unknown_remainder():
    rates = RateTable.load()
    usage = {
        "duration_usage_items": 2,
        "returned_duration_seconds": 90,
        "sent_audio_seconds": 600,
        "billing_usage_complete": False,
    }
    assert rates.estimate("gpt-live-transcribe", usage) == Decimal("0.0255")
    assert rates.estimate("gpt-live-transcribe", {"sent_audio_seconds": 600}) is None
    assert rates.estimate("gpt-live-transcribe", usage, service_tier="priority") is None
    assert rates.estimate("gpt-4o-mini-tts", usage) is None
    for invalid in [-1, True, float("nan"), float("inf"), "90"]:
        assert (
            rates.estimate("gpt-live-transcribe", {**usage, "returned_duration_seconds": invalid})
            is None
        )
    for invalid in [0, -1, True, 10001]:
        assert (
            rates.estimate("gpt-live-transcribe", {**usage, "duration_usage_items": invalid})
            is None
        )
    gate = ProviderGate(Settings(_env_file=None, iago_development_budget=0.02))
    gate.record("openai_stt", "session", usage, model="gpt-live-transcribe")
    gate.record("openai_stt", "session", usage, model="gpt-live-transcribe")
    assert gate.estimated_usd == pytest.approx(0.0255) and gate.unknown_charges == 1
    assert gate.usage[0]["usage_priced_completely"] is False
    with pytest.raises(ProviderError, match="development_budget"):
        gate.require("openai")
