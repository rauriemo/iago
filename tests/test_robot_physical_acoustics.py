"""Independent robot-speaker recordings; scoring may run off-device.

Declared physical provenance and human isolation review are required. This check
does not generate recordings or qualify Wi-Fi loss, motion, or integration load.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from reachy_brain.evals.acoustics import CutoffTrial, measure_cutoff, summarize_cutoffs


@pytest.mark.robot
@pytest.mark.features("C2", "D2", "D3", "D5")
@pytest.mark.scenario("ROBOT-PHYSICAL-RECORDED-CUTOFF")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
def test_independent_robot_cutoff_recordings(profile, record_property):
    variable = "IAGO_ROBOT_" + profile.upper() + "_ACOUSTIC_MANIFEST"
    configured = os.environ.get(variable)
    if not configured or not Path(configured).is_file():
        pytest.skip(
            f"{variable} required: independent physical robot speaker recordings for {profile}; "
            "see docs/iago/ROBOT_EVAL.md"
        )
    path = Path(configured).resolve()
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    assert len(data) <= 1024 * 1024, "acoustic manifest size limit"
    manifest = json.loads(data)
    assert manifest["fixture_kind"] == "physical-robot-independent-recording", (
        "physical robot recording required"
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
    trials = [CutoffTrial.model_validate(row) for row in manifest["trials"]]
    assert len({trial.id for trial in trials}) == len(trials), "duplicate trial IDs"
    assert len({(t.sha256, t.event_sample) for t in trials}) == len(trials), (
        "duplicate recorded events"
    )
    record_property("fixture_ids", [t.id for t in trials])
    record_property("sample_count", len(trials))
    record_property(
        "expected",
        "Independent robot speaker cutoff p95 <150 ms Stop and <300 ms spoken; "
        "both voices/fallback and >=20 spoken trials in the declared deployment profile",
    )
    measurements = []
    try:
        for trial in trials:
            measurements.append(measure_cutoff(path.parent, trial))
    finally:
        record_property(
            "measurements",
            {
                "manifest_sha256": hashlib.sha256(data).hexdigest(),
                "profile": profile,
                "trials": measurements,
                "groups": summarize_cutoffs(measurements),
                "declared_recording_metadata": {key: manifest[key] for key in metadata_fields},
                "scope": "Reviewed independent physical robot cutoff recordings only; "
                "no PC-loss/watchdog, motion, integration-load, echo or soak qualification",
            },
        )
    assert sum(t.action == "spoken" for t in trials) >= 20, "at least 20 spoken interruptions"
    groups = summarize_cutoffs(measurements)
    for provider in ("openai", "elevenlabs", "fallback"):
        for action in ("stop", "spoken"):
            assert f"{provider}/{action}" in groups, f"missing {provider}/{action} recording"
    assert all(group["passes_target"] for group in groups.values()), "physical cutoff target failed"
