"""Synthetic manifest attack: channels cannot multiply an interruption count."""

import json

import pytest
from test_acoustic_scoring import fixture
from test_physical_acoustics import test_independently_recorded_pc_cutoff_trials as score_pc
from test_robot_physical_acoustics import test_independent_robot_cutoff_recordings as score_robot


@pytest.mark.features("C2", "D2", "D3", "D5")
@pytest.mark.scenario("ACOUSTIC-CHANNEL-DUPLICATE-EVENT")
@pytest.mark.parametrize("profile", ["pc", "reachy_pc", "reachy_local"])
def test_another_channel_cannot_count_as_another_recorded_event(tmp_path, monkeypatch, profile):
    trial = fixture(tmp_path).model_dump()
    trials = [
        dict(trial, id=str(index), action="spoken", event_sample=16000 + index * 100)
        for index in range(20)
    ]
    trials[1].update(event_sample=trials[0]["event_sample"], channel=1)
    manifest = {
        "fixture_kind": "physical-pc-independent-recording"
        if profile == "pc"
        else "physical-robot-independent-recording",
        "profile": profile,
        **{
            key: "synthetic rejection fixture"
            for key in (
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
        },
        "trials": trials,
    }
    path = tmp_path / "synthetic-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    variable = (
        "IAGO_PC_ACOUSTIC_MANIFEST"
        if profile == "pc"
        else ("IAGO_ROBOT_" + profile.upper() + "_ACOUSTIC_MANIFEST")
    )
    monkeypatch.setenv(variable, str(path))
    with pytest.raises(AssertionError, match="duplicate recorded events"):
        if profile == "pc":
            score_pc(lambda *args: None)
        else:
            score_robot(profile, lambda *args: None)
