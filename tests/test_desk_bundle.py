"""Synthetic recording and snapshots verify binding, not natural desk behavior."""

import hashlib
import json

import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.evals.desk_bundle import DeskBundle, score_bundle


def synthetic_desk_bundle(tmp_path, case="valid", profile="pc"):
    def save(name, data):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        (tmp_path / name).write_bytes(raw)
        return {"file": name, "sha256": hashlib.sha256(raw).hexdigest()}

    engine = BehaviorEngine()
    snapshots = [engine.observations()]
    engine.offer(Event("wave", "camera", "wave_detected", 100, 0.99), now=100)
    snapshots.append(engine.observations())
    refs = {
        "snapshots": save("snapshots.json", snapshots),
        "recording": save("recording.bin", b"Synthetic recording for binding test only"),
    }
    review = {
        "profile": profile,
        "snapshots_sha256": refs["snapshots"]["sha256"],
        "recording_sha256": refs["recording"]["sha256"],
        "reviewer": "synthetic reviewer",
        "recorded_at": 1,
        "reviewed_at": 2,
        "source_kind": "independent_live_camera_recording",
        "complete_recording_reviewed": True,
        "greeting_delivery_reviewed": True,
        "started": 0,
        "ended": 1800,
        "natural_workload_reviewed": True,
        "detector_continuity_reviewed": True,
        "timeline_alignment_reviewed": True,
        "events": [
            dict(
                event="wave",
                source="camera",
                generation=0,
                classification="false",
                greeting_observed=False,
            )
        ],
    }
    if case == "binding":
        review["snapshots_sha256"] = "0" * 64
    if case == "review":
        review["complete_recording_reviewed"] = False
    if case == "chronology":
        review["reviewed_at"] = 0
    if case == "profile":
        review["profile"] = "reachy_pc"
    if case == "uncertain":
        review["events"][0]["classification"] = "uncertain"
    refs["review"] = save("review.json", review)
    if case in {"recording", "snapshots"}:
        (tmp_path / refs[case]["file"]).write_bytes(b"changed")
    return DeskBundle.model_validate(refs)


@pytest.mark.features("P1", "P3", "P4", "P8", "D6")
@pytest.mark.scenario("DESK-RECORDING-BINDING")
@pytest.mark.parametrize(
    "case", ["valid", "recording", "snapshots", "binding", "review", "chronology", "profile"]
)
def test_desk_recording_review_binding(tmp_path, case):
    bundle = synthetic_desk_bundle(tmp_path, case)
    if case == "valid":
        result = score_bundle(tmp_path, bundle, profile="pc")
        assert result["review_complete"] and result["groups"]["wave_detected"]["false"] == 1
        assert not result["physical_qualification"]
        assert "synthetic reviewer" not in str(result)
    else:
        with pytest.raises(ValueError):
            score_bundle(tmp_path, bundle, profile="pc")
