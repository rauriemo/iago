"""Synthetic bound records test evaluator integrity, not physical origin."""

import hashlib
import json

import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.evals.live_presence import EntryPlan
from reachy_brain.evals.presence_bundle import PresenceBundle, frozen_labels, score_bundle


def synthetic_presence_bundle(tmp_path, case="valid", profile="pc"):
    def save(name, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        (tmp_path / name).write_bytes(raw)
        return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}

    plan = EntryPlan.model_validate(
        {
            "cases": [
                dict(
                    id=str(i),
                    label="entry" if i < 10 else "startup" if i == 10 else "occlusion",
                    start=float(i * 10),
                    end=float(i * 10 + 10),
                    source="camera",
                    generation=0,
                )
                for i in range(12)
            ]
        }
    )
    if case == "numeric_failure":
        plan.cases[0].source = "wrong-source"
    engine = BehaviorEngine()
    refs = {
        "plan": save("plan.json", plan.model_dump()),
        "start": save("start.json", engine.observations()),
    }
    for i in range(10):
        engine.offer(
            Event(str(i), "camera", "person_entered_view", float(i * 10 + 1), 0.99),
            now=float(i * 10 + 1),
        )
    refs["end"] = save("end.json", engine.observations())
    refs["recording"] = save("recording.bin", b"Synthetic recording placeholder, binding test only")
    review = {
        "profile": profile,
        **{k + "_sha256": v["sha256"] for k, v in refs.items()},
        "frozen_labels_sha256": frozen_labels(plan),
        "frozen_at": 1,
        "recorded_at": 2,
        "reviewed_at": 3,
        "observation_start": 0,
        "observation_end": 120,
        "reviewer": "synthetic reviewer",
        "source_kind": "independent_live_camera_recording",
        "reviewed_case_ids": [c.id for c in plan.cases],
        "greetings": [
            dict(id=str(i), source="camera", generation=0, greeted=False) for i in range(10)
        ],
        **dict.fromkeys(
            [
                "live_source_reviewed",
                "detector_continuity_reviewed",
                "timeline_alignment_reviewed",
                "labels_reviewed",
                "greeting_delivery_reviewed",
            ],
            True,
        ),
    }
    if case == "labels":
        review["frozen_labels_sha256"] = "0" * 64
    if case == "coverage":
        review["observation_end"] = 110
    if case == "chronology":
        review["frozen_at"] = 4
    if case == "review":
        review["detector_continuity_reviewed"] = False
    if case == "profile":
        review["profile"] = "reachy_pc"
    refs["review"] = save("review.json", review)
    if case == "recording":
        (tmp_path / "recording.bin").write_bytes(b"changed")
    return PresenceBundle.model_validate(refs)


@pytest.mark.features("P1", "P4", "P6", "D6")
@pytest.mark.scenario("PRESENCE-RECORDING-REVIEW-BINDING")
@pytest.mark.parametrize(
    "case", ["valid", "recording", "labels", "coverage", "chronology", "review", "profile"]
)
def test_presence_artifact_review_binding(tmp_path, case):
    bundle = synthetic_presence_bundle(tmp_path, case)
    if case == "valid":
        result = score_bundle(tmp_path, bundle, profile="pc")
        assert result["passes_numeric_targets"] and not result["physical_qualification"]
        assert "synthetic reviewer" not in str(result)
    else:
        with pytest.raises(ValueError):
            score_bundle(tmp_path, bundle, profile="pc")
