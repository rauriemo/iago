"""Robot E1 priority recordings; each physical profile requires its own reviewed evidence."""

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


@pytest.mark.robot
@pytest.mark.features("C2", "D2", "D3", "E1")
@pytest.mark.scenario("ROBOT-INTEGRATION-BLOCKED-CUTOFF")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
def test_robot_recorded_integration_cutoffs(profile, record_property):
    variable = "IAGO_ROBOT_" + profile.upper() + "_INTEGRATION_ACOUSTIC_MANIFEST"
    configured = os.environ.get(variable)
    if not configured:
        pytest.skip(
            f"{variable} required: independent physical robot recordings "
            "with reviewed blocked-activity traces; see docs/iago/ROBOT_EVAL.md"
        )
    path = Path(configured).resolve()
    if not path.is_file():
        pytest.skip("Configured integration acoustic manifest is unavailable")
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    assert len(data) <= 1024 * 1024, "integration acoustic manifest size limit"
    manifest = json.loads(data)
    assert manifest["fixture_kind"] == "physical-robot-independent-integration-recording", (
        "physical robot integration recording required"
    )
    assert manifest["profile"] == profile, "Recordings cannot qualify another deployment profile"
    metadata_fields = (
        "operator",
        "recorded_at",
        "application_revision",
        "devices",
        "voice_ids",
        "robot_identity",
        "daemon_version",
        "configuration_sha256",
        "reviewer",
    )
    for field in metadata_fields:
        assert manifest.get(field), f"missing {field}"
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
                "profile": profile,
                "trials": measurements,
                "summary": summarize_integration_cutoffs(measurements),
                "declared_recording_metadata": {k: manifest[k] for k in metadata_fields},
                "scope": "Recorded robot cutoff during reviewed blocked activities in the declared profile; no motion, PC-loss/watchdog, echo or soak qualification",
            },
        )
    assert summarize_integration_cutoffs(measurements)["passes_target"], (
        "missing activity/provider/action coverage, insufficient spoken trials or cutoff target failure"
    )
