"""Synthetic review declarations and files, never independently reviewed physical work."""

import hashlib
import json

import pytest
from test_workload_bundle import bundle_files

from reachy_brain.evals.workload_review import ReviewedWorkloadBundle, score_reviewed_bundle


def reviewed_bundle_files(tmp_path, fault="none", *, origin="synthetic"):
    bundle = bundle_files(
        tmp_path,
        "bounds"
        if fault == "component_failure"
        else "missing_timing"
        if fault == "component_blocked"
        else "none",
    ).model_dump()
    if origin != "synthetic":
        path = tmp_path / "activities.json"
        data = json.loads(path.read_bytes())
        data["origin"] = origin
        raw = json.dumps(data).encode()
        path.write_bytes(raw)
        bundle["activities"]["sha256"] = hashlib.sha256(raw).hexdigest()
    for name in ("plan", "recording", "browser_resources", "operation_evidence", "task_evidence"):
        data = ("Synthetic " + name).encode()
        file = name + ".txt"
        (tmp_path / file).write_bytes(data)
        bundle[name] = {"file": file, "sha256": hashlib.sha256(data).hexdigest()}
    activities = json.loads((tmp_path / "activities.json").read_bytes())["activities"]
    review = {
        "profile": "pc",
        "reviewer": "synthetic-private-reviewer",
        "artifacts": {name: artifact["sha256"] for name, artifact in bundle.items()},
        "frozen_at": 1.0,
        "recorded_at": 2.0,
        "reviewed_at": 5403.0,
        "reviewed_activity_ids": [event["id"] for event in activities],
    }
    for key in (
        "timeline_alignment_reviewed",
        "complete_recording_reviewed",
        "workload_and_source_continuity_reviewed",
        "activity_outcomes_and_authorization_reviewed",
        "browser_and_process_memory_reviewed",
        "stable_memory_after_warmup_reviewed",
        "timing_scenario_coverage_reviewed",
    ):
        review[key] = True
    if fault == "binding":
        review["artifacts"]["capture"] = "0" * 64
    elif fault == "coverage":
        review["reviewed_activity_ids"].pop()
    elif fault == "duplicate":
        review["reviewed_activity_ids"].append(review["reviewed_activity_ids"][0])
    elif fault == "chronology":
        review["frozen_at"] = 2.0
    elif fault == "premature_review":
        review["reviewed_at"] = 3.0
    elif fault == "recording":
        (tmp_path / "recording.txt").write_bytes(b"changed")
    elif fault == "review_flag":
        review["stable_memory_after_warmup_reviewed"] = False
    data = json.dumps(review).encode()
    (tmp_path / "review.json").write_bytes(data)
    bundle["review"] = {"file": "review.json", "sha256": hashlib.sha256(data).hexdigest()}
    return ReviewedWorkloadBundle.model_validate(bundle)


@pytest.mark.features("D6", "E1", "K1", "V9", "P10")
@pytest.mark.scenario("WORKLOAD-REVIEW-ARTIFACT-BINDING")
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "binding",
        "coverage",
        "duplicate",
        "chronology",
        "premature_review",
        "recording",
        "review_flag",
        "component_failure",
    ],
)
def test_review_binds_every_artifact_and_preserves_component_outcomes(tmp_path, fault):
    bound = reviewed_bundle_files(tmp_path, fault)
    activities = json.loads((tmp_path / "activities.json").read_bytes())["activities"]
    if fault not in {"none", "component_failure"}:
        with pytest.raises(ValueError):
            score_reviewed_bundle(tmp_path, bound)
        return
    result = score_reviewed_bundle(tmp_path, bound)
    assert result["status"] == ("fail" if fault == "component_failure" else "review_required")
    assert result["declared_review_bound"] and result["reviewed_activity_count"] == len(activities)
    assert not result["acceptance_pass"] and not result["physical_qualification"]
    assert "synthetic-private-reviewer" not in json.dumps(result)
