"""Synthetic artifacts test binding, never physical motion qualification."""

import hashlib
import json

import pytest
from test_robot_motion_scoring import trace

from reachy_brain.evals.robot_motion import CHANNELS
from reachy_brain.evals.robot_motion_bundle import MotionBundle, score_bundle


def bundle(root):
    def save(name, data):
        raw = json.dumps(data).encode() if not isinstance(data, bytes) else data
        (root / name).write_bytes(raw)
        return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}

    t = save("trace.json", trace())
    c = save(
        "calibration.json",
        {"channels": list(CHANNELS), "measurement_error": [0.001] * 8, "frozen_at": 1},
    )
    rec = save("recording.bin", b"Explicit synthetic recording placeholder for binding test only")
    review = {
        "trace_sha256": t["sha256"],
        "calibration_sha256": c["sha256"],
        "recording_sha256": rec["sha256"],
        "recorded_at": 2,
        "source_kind": "independent_pose_recording",
        "reviewer": "Synthetic reviewer",
        "calibration_reviewed": True,
        "event_alignment_reviewed": True,
        "pose_extraction_reviewed": True,
    }
    r = save("review.json", review)
    return (
        MotionBundle.model_validate({"trace": t, "calibration": c, "recording": rec, "review": r}),
        save,
        review,
    )


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-ARTIFACT-BINDING")
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "trace_changed",
        "recording_changed",
        "review_changed",
        "outside",
        "unreviewed",
        "wrong_binding",
    ],
)
def test_bound_motion_artifacts(tmp_path, case):
    b, save, review = bundle(tmp_path)
    if case.endswith("_changed"):
        artifact = getattr(b, case.removesuffix("_changed"))
        (tmp_path / artifact.file).write_bytes(b"changed")
    if case == "outside":
        b.trace.file = "../trace.json"
    if case in ("unreviewed", "wrong_binding"):
        review["pose_extraction_reviewed" if case == "unreviewed" else "trace_sha256"] = (
            False if case == "unreviewed" else "0" * 64
        )
        b.review = type(b.review).model_validate(save("review.json", review))
    if case == "valid":
        result = score_bundle(tmp_path, b)
        assert result["declared_review_bound"] and not result["physical_origin_verified"]
        assert result["pre_event_motion_observed"] and not result["motion_observed_after_lease"]
        assert "Synthetic reviewer" not in str(result)
    else:
        with pytest.raises(ValueError):
            score_bundle(tmp_path, b)


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-TRIGGER-PROFILE-BINDING")
@pytest.mark.parametrize("wrong", [None, "trigger", "profile"])
def test_review_cannot_be_relabelled_for_another_trigger_or_profile(tmp_path, wrong):
    b, save, review = bundle(tmp_path)
    review.update(trigger="pc_pause", profile="reachy_pc")
    b.review = type(b.review).model_validate(save("review.json", review))
    options = {"expected_trigger": "pc_pause", "expected_profile": "reachy_pc"}
    if wrong == "trigger":
        options["expected_trigger"] = "pc_terminate"
    if wrong == "profile":
        options["expected_profile"] = "reachy_local"
    if wrong:
        with pytest.raises(ValueError, match="binding_mismatch"):
            score_bundle(tmp_path, b, **options)
    else:
        assert score_bundle(tmp_path, b, **options)["declared_review_bound"]
