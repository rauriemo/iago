"""Score externally captured physical trials; absent recordings are explicitly blocked."""

import json
import os
from pathlib import Path

import pytest

from reachy_brain.evals.acoustics import CutoffTrial, measure_cutoff, summarize_cutoffs


@pytest.mark.live_pc
@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("PHYSICAL-PC-RECORDED-CUTOFF")
def test_independently_recorded_pc_cutoff_trials(record_property):
    configured = os.environ.get("IAGO_PC_ACOUSTIC_MANIFEST")
    if not configured:
        pytest.skip(
            "IAGO_PC_ACOUSTIC_MANIFEST required: independent physical speaker recordings, calibrated event alignment and human-reviewed source isolation; see docs/iago/ACOUSTIC_EVAL.md"
        )
    path = Path(configured).resolve()
    if not path.is_file():
        pytest.skip("Configured acoustic manifest is unavailable")
    assert path.stat().st_size <= 1024 * 1024, "acoustic manifest size limit"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["fixture_kind"] == "physical-pc-independent-recording"
    assert manifest["operator"] and manifest["recorded_at"] and manifest["application_revision"]
    assert manifest["devices"] and manifest["voice_ids"]
    trials = [CutoffTrial.model_validate(t) for t in manifest["trials"]]
    assert 1 <= len(trials) <= 1000
    assert len({t.id for t in trials}) == len(trials), "duplicate trial IDs"
    assert len({(t.sha256, t.event_sample) for t in trials}) == len(trials), (
        "duplicate recorded events"
    )
    assert sum(t.action == "spoken" for t in trials) >= 20, "at least 20 spoken interruptions"
    measurements = [measure_cutoff(path.parent, t) for t in trials]
    groups = summarize_cutoffs(measurements)
    record_property("sample_count", len(trials))
    record_property(
        "measurements",
        {
            "trials": measurements,
            "groups": groups,
            "declared_recording_metadata": {
                k: manifest[k]
                for k in ("operator", "recorded_at", "application_revision", "devices", "voice_ids")
            },
        },
    )
    record_property(
        "expected",
        "Independent recorded cutoff p95 <150 ms Stop and <300 ms spoken; both voices and fallback; >=20 spoken trials",
    )
    for provider in ("openai", "elevenlabs", "fallback"):
        for action in ("stop", "spoken"):
            assert f"{provider}/{action}" in groups, f"missing {provider}/{action} recording"
    assert all(g["passes_target"] for g in groups.values()), "physical cutoff target failed"
