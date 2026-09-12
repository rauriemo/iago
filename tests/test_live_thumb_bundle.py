"""Synthetic bundle integrity tests; no physical gesture qualification."""

import hashlib
import json

import pytest
from test_live_thumb_scoring import dataset

from reachy_brain.evals.live_thumb_bundle import LiveThumbBundle, label_digest, score_bundle


def synthetic_bundle(tmp_path, case="valid", profile="pc", scoring_case="valid"):
    plan, start, end = dataset(scoring_case)
    plan.profile = profile

    def save(name, data):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        (tmp_path / name).write_bytes(raw)
        return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}

    refs = {
        name: save(name + ".json", value.model_dump())
        for name, value in [("plan", plan), ("start", start), ("end", end)]
    }
    refs["recording"] = save(
        "recording.bin", b"Explicit synthetic recording placeholder; binding test only"
    )
    review = {
        "profile": profile,
        **{key + "_sha256": value["sha256"] for key, value in refs.items()},
        "frozen_labels_sha256": label_digest(plan),
        "frozen_at": 1,
        "recorded_at": 2,
        "reviewed_at": 3,
        "reviewer": "synthetic reviewer",
        "source_kind": "independent_live_camera_recording",
        "reviewed_case_ids": [c.id for c in plan.cases],
        **dict.fromkeys(
            [
                "live_source_reviewed",
                "question_eligibility_reviewed",
                "timeline_alignment_reviewed",
                "feedback_reviewed",
                "complete_observation_reviewed",
            ],
            True,
        ),
    }
    if case == "labels":
        review["frozen_labels_sha256"] = "0" * 64
    if case == "review":
        review["feedback_reviewed"] = False
    if case == "chronology":
        review["frozen_at"] = 4
    if case == "cases":
        review["reviewed_case_ids"][-1] = "0"
    if case == "profile":
        review["profile"] = "reachy_pc"
    refs["review"] = save("review.json", review)
    if case == "recording":
        (tmp_path / "recording.bin").write_bytes(b"changed")
    if case == "outside":
        refs["plan"]["file"] = "../outside.json"
    return LiveThumbBundle.model_validate(refs)


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("LIVE-THUMB-REVIEW-BINDING")
@pytest.mark.parametrize(
    "case", ["valid", "recording", "labels", "review", "chronology", "cases", "profile", "outside"]
)
def test_bound_live_thumb_artifacts(tmp_path, case):
    bundle = synthetic_bundle(tmp_path, case)
    if case == "valid":
        result = score_bundle(tmp_path, bundle, profile="pc")
        assert result["passes_numeric_targets"] and result["declared_review_bound"]
        assert not result["physical_gesture_validated"] and not result["release_validated"]
        assert "synthetic reviewer" not in str(result)
    else:
        with pytest.raises(ValueError):
            score_bundle(tmp_path, bundle, profile="pc")
