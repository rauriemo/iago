"""Bind independent workload review artifacts; declarations do not prove physical execution."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from .robot_motion_bundle import Artifact, read_bound
from .screen_grounding import StrictRecord
from .workload_bundle import WorkloadBundle, score_bundle
from .workload_coverage import WorkloadRun


class ReviewedWorkloadBundle(WorkloadBundle):
    plan: Artifact
    recording: Artifact
    browser_resources: Artifact
    operation_evidence: Artifact
    task_evidence: Artifact
    review: Artifact


class WorkloadReview(StrictRecord):
    profile: Literal["pc"]
    reviewer: str = Field(min_length=1, max_length=128)
    artifacts: dict[str, str]
    frozen_at: float = Field(ge=0)
    recorded_at: float = Field(ge=0)
    reviewed_at: float = Field(ge=0)
    reviewed_activity_ids: list[str] = Field(max_length=10000)
    timeline_alignment_reviewed: Literal[True]
    complete_recording_reviewed: Literal[True]
    workload_and_source_continuity_reviewed: Literal[True]
    activity_outcomes_and_authorization_reviewed: Literal[True]
    browser_and_process_memory_reviewed: Literal[True]
    stable_memory_after_warmup_reviewed: Literal[True]
    timing_scenario_coverage_reviewed: Literal[True]


def score_reviewed_bundle(root: Path, bundle: ReviewedWorkloadBundle):
    review = WorkloadReview.model_validate_json(read_bound(root, bundle.review))
    artifacts = bundle.model_dump(exclude={"review"})
    expected = {name: artifact["sha256"] for name, artifact in artifacts.items()}
    if review.artifacts != expected:
        raise ValueError("workload_review_binding_mismatch")
    if not review.frozen_at < review.recorded_at <= review.reviewed_at:
        raise ValueError("workload_review_chronology")
    run = WorkloadRun.model_validate_json(
        read_bound(root, bundle.activities, limit=4 * 1024 * 1024)
    )
    if review.reviewed_at < review.recorded_at + run.duration:
        raise ValueError("workload_review_before_run_end")
    ids = review.reviewed_activity_ids
    if len(set(ids)) != len(ids) or set(ids) != {event.id for event in run.activities}:
        raise ValueError("workload_review_activity_coverage")
    sizes = {}
    for name in ("plan", "recording", "browser_resources", "operation_evidence", "task_evidence"):
        sizes[name] = read_bound(
            root,
            getattr(bundle, name),
            limit=512 * 1024 * 1024 if name == "recording" else 32 * 1024 * 1024,
            retain=False,
        )
    result = score_bundle(
        root, WorkloadBundle(capture=bundle.capture, activities=bundle.activities)
    )
    return {
        **result,
        "artifacts": bundle.model_dump(),
        "supporting_artifact_bytes": sizes,
        "declared_review_bound": True,
        "reviewed_activity_count": len(ids),
        "review_limitation": "Exact hashes, activity coverage and declared chronology/review only. Independently substantiate recording origin, plan freeze, actual operation outcomes, source continuity and resource/timing review. Separate acoustic and full-product gates remain required; no physical qualification or acceptance pass.",
    }
