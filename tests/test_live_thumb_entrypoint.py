"""Synthetic direct record-gate tests; no physical tier qualification."""

import json

import pytest
from test_live_thumb_bundle import synthetic_bundle
from test_physical_thumbs import evaluate_recorded_thumbs


@pytest.mark.features("P10", "D5", "D6")
@pytest.mark.scenario("LIVE-THUMB-ENTRYPOINT-PROFILES")
@pytest.mark.parametrize("profile", ["pc", "reachy_pc", "reachy_local"])
@pytest.mark.parametrize("case", ["valid", "missing", "numeric_failure", "wrong_profile"])
def test_reviewed_gate_profiles_and_failures(tmp_path, monkeypatch, profile, case):
    variable = "IAGO_" + profile.upper() + "_LIVE_THUMB_MANIFEST"
    monkeypatch.delenv(variable, raising=False)
    properties = {}
    if case == "missing":
        with pytest.raises(pytest.skip.Exception, match=variable):
            evaluate_recorded_thumbs(properties.__setitem__, profile)
        assert not properties
        return
    bundle = synthetic_bundle(
        tmp_path, profile=profile, scoring_case="miss" if case == "numeric_failure" else "valid"
    )
    manifest = {
        "fixture_kind": "physical-pc-independent-recording"
        if profile == "pc"
        else "physical-robot-independent-recording",
        "profile": profile,
        "bundle": bundle.model_dump(),
        **dict.fromkeys(
            [
                "operator",
                "reviewer",
                "recorded_at",
                "application_revision",
                "devices",
                "robot_identity",
                "daemon_version",
                "configuration_sha256",
            ],
            "synthetic declaration only",
        ),
    }
    if case == "wrong_profile":
        manifest["fixture_kind"] = "incorrect-platform"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv(variable, str(path))
    if case == "valid":
        evaluate_recorded_thumbs(properties.__setitem__, profile)
        assert properties["sample_count"] == 60
        assert not properties["measurements"]["physical_gesture_validated"]
    else:
        with pytest.raises(
            AssertionError,
            match="numeric/context" if case == "numeric_failure" else "platform mismatch",
        ):
            evaluate_recorded_thumbs(properties.__setitem__, profile)
        if case == "numeric_failure":
            assert properties["sample_count"] == 60
            assert not properties["measurements"]["passes_numeric_targets"]
