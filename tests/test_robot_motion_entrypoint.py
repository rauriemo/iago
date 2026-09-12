"""Exercise the physical record gate with explicitly synthetic artifacts only."""

import json

import pytest
import test_robot_physical_motion as entrypoint
from test_robot_motion_bundle import bundle


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-MISSING-PREREQUISITE")
@pytest.mark.parametrize("configured", [False, True])
def test_missing_motion_recording_never_passes(tmp_path, monkeypatch, configured):
    monkeypatch.delenv("IAGO_ROBOT_MOTION_MANIFEST", raising=False)
    if configured:
        monkeypatch.setenv("IAGO_ROBOT_MOTION_MANIFEST", str(tmp_path / "absent.json"))
    properties = {}
    with pytest.raises(pytest.skip.Exception, match="IAGO_ROBOT_MOTION_MANIFEST required"):
        entrypoint.test_recorded_pc_loss_motion(properties.__setitem__)
    assert properties == {}


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-ENTRYPOINT-BOUNDARIES")
@pytest.mark.parametrize(
    "case",
    ["valid", "lease", "idle", "late", "profile", "duplicate_id", "missing_trigger"],
)
def test_motion_record_gate_with_synthetic_files(tmp_path, monkeypatch, case):
    trials = []
    for index, trigger in enumerate(("pc_pause", "pc_terminate", "connection_loss")):
        root = tmp_path / str(index)
        root.mkdir()
        artifact, save, review = bundle(root)
        trace = json.loads((root / "trace.json").read_text())
        # Separate synthetic traces, each bound to its own review and trigger.
        trace["samples"][0]["pose"][1] = index * 0.01
        if index == 0:
            if case == "lease":
                trace["lease_seconds"] = 0.9
            if case == "idle":
                for sample in trace["samples"]:
                    sample["pose"] = [0.0] * 8
            if case == "late":
                trace["samples"][-1]["pose"][7] = 0.1
        artifact.trace = type(artifact.trace).model_validate(save("trace.json", trace))
        review.update(
            trigger=trigger,
            profile="reachy_local" if case == "profile" and index == 0 else "reachy_pc",
            trace_sha256=artifact.trace.sha256,
        )
        artifact.review = type(artifact.review).model_validate(save("review.json", review))
        data = artifact.model_dump()
        for reference in data.values():
            reference["file"] = f"{index}/" + reference["file"]
        trials.append({"id": str(index), "trigger": trigger, "bundle": data})
    if case == "duplicate_id":
        trials[1]["id"] = trials[0]["id"]
    if case == "missing_trigger":
        trials[2]["trigger"] = "pc_pause"
    manifest = {
        "fixture_kind": "physical-robot-independent-recording",
        "profile": "reachy_pc",
        **dict.fromkeys(
            [
                "robot_identity",
                "daemon_version",
                "configuration_sha256",
                "operator",
                "reviewer",
                "recorded_at",
                "application_revision",
                "devices",
            ],
            "synthetic test declaration only",
        ),
        "trials": trials,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv("IAGO_ROBOT_MOTION_MANIFEST", str(path))
    properties = {}
    if case == "valid":
        entrypoint.test_recorded_pc_loss_motion(properties.__setitem__)
        assert properties["sample_count"] == 3
        for row in properties["measurements"]["trials"]:
            assert not row["physical_origin_verified"] and not row["release_validated"]
    else:
        message = {
            "lease": "current runtime lease",
            "idle": "idle trace",
            "late": "observed motion after lease",
            "profile": "motion_profile_binding_mismatch",
            "duplicate_id": "duplicate trial IDs",
            "missing_trigger": "missing loss trigger",
        }[case]
        with pytest.raises((AssertionError, ValueError), match=message):
            entrypoint.test_recorded_pc_loss_motion(properties.__setitem__)
        if case in {"lease", "idle", "late"}:
            assert properties["sample_count"] == 1
            assert len(properties["measurements"]["trials"]) == 1
