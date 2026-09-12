"""Synthetic provenance rejection only; no physical recording is scored."""

import pytest
from test_physical_opening_words import validate_profile


@pytest.mark.features("C2", "D2", "D3")
@pytest.mark.scenario("OPENING-RECORDING-PROFILE-ISOLATION")
@pytest.mark.parametrize("change", ["pc", "other_profile", "identity", "daemon", "configuration"])
def test_opening_recordings_cannot_cross_robot_profile(change):
    manifest = {
        "fixture_kind": "physical-robot-independent-recording",
        "profile": "reachy_pc",
        "robot_identity": "synthetic",
        "daemon_version": "synthetic",
        "configuration_sha256": "synthetic",
    }
    if change == "pc":
        manifest["fixture_kind"] = "physical-pc-independent-recording"
        expected = "platform mismatch"
    elif change == "other_profile":
        manifest["profile"] = "reachy_local"
        expected = "deployment profile mismatch"
    else:
        key = {
            "identity": "robot_identity",
            "daemon": "daemon_version",
            "configuration": "configuration_sha256",
        }[change]
        del manifest[key]
        expected = "missing " + key
    with pytest.raises(AssertionError, match=expected):
        validate_profile(manifest, "reachy_pc")
