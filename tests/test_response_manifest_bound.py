"""Synthetic manifest replacement cannot bypass the physical scorer input cap."""

import json
from types import SimpleNamespace

import pytest
from test_physical_response_latency import score_recorded_responses
from test_physical_response_latency import test_independently_recorded_response_latency as score


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("RESPONSE-MANIFEST-BOUNDED-SNAPSHOT")
def test_response_manifest_does_not_trust_earlier_file_size(tmp_path, monkeypatch):
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (1024 * 1024) + b"{}")
    from pathlib import Path

    original = Path.stat

    def stale_stat(self, *args, **kwargs):
        actual = original(self, *args, **kwargs)
        if self == path:
            return SimpleNamespace(st_size=2, st_mode=actual.st_mode)
        return actual

    monkeypatch.setattr(Path, "stat", stale_stat)
    monkeypatch.setenv("IAGO_PC_RESPONSE_MANIFEST", str(path))
    with pytest.raises(AssertionError, match="manifest size limit"):
        score(lambda *args: None)


@pytest.mark.features("C1", "D2", "D3", "D6")
@pytest.mark.scenario("ROBOT-RESPONSE-PROFILE-BOUNDARY")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
@pytest.mark.parametrize("change", ["pc", "other_robot"])
def test_response_entrypoint_rejects_another_recording_profile(
    tmp_path, monkeypatch, profile, change
):
    manifest = {
        "fixture_kind": "physical-pc-independent-recording"
        if change == "pc"
        else "physical-robot-independent-recording",
        "profile": "reachy_local" if profile == "reachy_pc" else "reachy_pc",
    }
    path = tmp_path / "synthetic-profile.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("IAGO_ROBOT_" + profile.upper() + "_RESPONSE_MANIFEST", str(path))
    with pytest.raises(
        AssertionError, match="platform mismatch" if change == "pc" else "profile mismatch"
    ):
        score_recorded_responses(lambda *args: None, profile=profile)
