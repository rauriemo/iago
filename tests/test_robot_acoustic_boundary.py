"""Synthetic manifest rejection; these checks do not score physical recordings."""

import json

import pytest
from test_robot_integration_acoustics import (
    test_robot_recorded_integration_cutoffs as score_integration,
)
from test_robot_physical_acoustics import test_independent_robot_cutoff_recordings as score_robot


@pytest.mark.features("D2", "D3", "C2", "E1")
@pytest.mark.scenario("ROBOT-ACOUSTIC-FIXTURE-BOUNDARY")
@pytest.mark.parametrize("change", ["pc", "other_profile", "missing_reviewer", "missing_identity"])
@pytest.mark.parametrize("integration", [False, True])
def test_robot_cutoff_rejects_wrong_provenance_before_scoring(
    tmp_path, monkeypatch, change, integration
):
    manifest = {
        "fixture_kind": "physical-robot-independent-recording",
        "profile": "reachy_pc",
        "operator": "synthetic",
        "recorded_at": "synthetic",
        "application_revision": "synthetic",
        "devices": "synthetic",
        "voice_ids": "synthetic",
        "robot_identity": "synthetic",
        "daemon_version": "synthetic",
        "configuration_sha256": "synthetic",
        "reviewer": "synthetic",
        "trials": [],
    }
    if integration:
        manifest["fixture_kind"] = "physical-robot-independent-integration-recording"
    if change == "pc":
        manifest["fixture_kind"] = "physical-pc-independent-recording"
        expected = (
            "physical robot integration recording required"
            if integration
            else "physical robot recording required"
        )
    elif change == "other_profile":
        manifest["profile"] = "reachy_local"
        expected = "another deployment profile"
    else:
        key = "reviewer" if change == "missing_reviewer" else "robot_identity"
        del manifest[key]
        expected = "missing " + key
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    suffix = "INTEGRATION_ACOUSTIC_MANIFEST" if integration else "ACOUSTIC_MANIFEST"
    monkeypatch.setenv("IAGO_ROBOT_REACHY_PC_" + suffix, str(path))
    with pytest.raises(AssertionError, match=expected):
        (score_integration if integration else score_robot)("reachy_pc", lambda *args: None)
