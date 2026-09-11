"""Physical E1 priority gate; private recordings are required, never synthesized here."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from reachy_brain.evals.integration_acoustics import (
    IntegrationCutoffTrial,
    measure_integration_cutoff,
    summarize_integration_cutoffs,
    validate_trial_identity,
)


@pytest.mark.live_pc
@pytest.mark.features("C2", "E1")
@pytest.mark.scenario("PHYSICAL-PC-INTEGRATION-BLOCKED-CUTOFF")
def test_recorded_cutoffs_during_blocked_integration_work(record_property):
    configured = os.environ.get("IAGO_PC_INTEGRATION_ACOUSTIC_MANIFEST")
    if not configured:
        pytest.skip(
            "IAGO_PC_INTEGRATION_ACOUSTIC_MANIFEST required: independent physical recordings "
            "with reviewed blocked-activity traces; see docs/iago/ACOUSTIC_EVAL.md"
        )
    path = Path(configured).resolve()
    if not path.is_file():
        pytest.skip("Configured integration acoustic manifest is unavailable")
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    assert len(data) <= 1024 * 1024, "integration acoustic manifest size limit"
    manifest = json.loads(data)
    assert manifest["fixture_kind"] == "physical-pc-independent-integration-recording"
    for field in ("operator", "recorded_at", "application_revision", "devices", "voice_ids"):
        assert manifest[field], f"missing {field}"
    assert isinstance(manifest["trials"], list) and 1 <= len(manifest["trials"]) <= 1000
    trials = [IntegrationCutoffTrial.model_validate(t) for t in manifest["trials"]]
    validate_trial_identity(trials)
    record_property("fixture_ids", [t.id for t in trials])
    record_property("sample_count", len(trials))
    record_property(
        "expected",
        "All five blocked activities x three voice paths x Stop/spoken; >=20 spoken; "
        "each group p95 strictly <150 ms Stop or <300 ms spoken",
    )
    measurements = []
    try:
        for trial in trials:
            measurements.append(measure_integration_cutoff(path.parent, trial))
    finally:
        record_property(
            "measurements",
            {
                "manifest_sha256": hashlib.sha256(data).hexdigest(),
                "trials": measurements,
                "summary": summarize_integration_cutoffs(measurements),
                "declared_recording_metadata": {
                    k: manifest[k]
                    for k in (
                        "operator",
                        "recorded_at",
                        "application_revision",
                        "devices",
                        "voice_ids",
                    )
                },
                "scope": "Recorded PC cutoff during reviewed blocked activities; no robot or soak qualification",
            },
        )
    assert summarize_integration_cutoffs(measurements)["passes_target"], (
        "missing activity/provider/action coverage, insufficient spoken trials or cutoff target failure"
    )
