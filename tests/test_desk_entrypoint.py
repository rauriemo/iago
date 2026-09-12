"""Synthetic direct entrypoint checks, never physical qualification."""

import json

import pytest
from test_desk_bundle import synthetic_desk_bundle
from test_physical_desk import evaluate_recorded_desk


@pytest.mark.features("P1", "P3", "P4", "P8", "D5", "D6")
@pytest.mark.scenario("DESK-ENTRYPOINT-PROFILES")
@pytest.mark.parametrize("profile", ["pc", "reachy_pc", "reachy_local"])
@pytest.mark.parametrize("case", ["valid", "missing", "uncertain"])
def test_profile_records_and_missing_prerequisites(tmp_path, monkeypatch, profile, case):
    variable = "IAGO_" + profile.upper() + "_DESK_MANIFEST"
    monkeypatch.delenv(variable, raising=False)
    properties = {}
    if case == "missing":
        with pytest.raises(pytest.skip.Exception, match=variable):
            evaluate_recorded_desk(properties.__setitem__, profile)
        assert not properties
        return
    bundle = synthetic_desk_bundle(tmp_path, case=case, profile=profile)
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
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv(variable, str(path))
    if case == "uncertain":
        with pytest.raises(AssertionError, match="review incomplete"):
            evaluate_recorded_desk(properties.__setitem__, profile)
    else:
        evaluate_recorded_desk(properties.__setitem__, profile)
    assert properties["sample_count"] == 1
    assert properties["measurements"]["review_complete"] == (case == "valid")
    assert not properties["measurements"]["physical_qualification"]
