"""Bounded reviewed entry artifacts; declarations do not authenticate physical origin."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .behavior_observation import BehaviorSnapshot
from .gesture_observation import Identifier
from .live_presence import EntryPlan, GreetingReview, score_observed_entries
from .robot_motion_bundle import Artifact, read_bound
from .screen_grounding import StrictRecord


class PresenceBundle(StrictRecord):
    plan: Artifact
    start: Artifact
    end: Artifact
    recording: Artifact
    review: Artifact


class PresenceReview(StrictRecord):
    profile: Literal["pc", "reachy_pc", "reachy_local"]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    end_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recording_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_at: float = Field(ge=0)
    recorded_at: float = Field(ge=0)
    reviewed_at: float = Field(ge=0)
    observation_start: float = Field(ge=0)
    observation_end: float = Field(gt=0)
    reviewer: Identifier
    source_kind: Literal["independent_live_camera_recording"]
    reviewed_case_ids: list[Identifier] = Field(min_length=12, max_length=200)
    greetings: list[GreetingReview] = Field(max_length=1000)
    live_source_reviewed: Literal[True]
    detector_continuity_reviewed: Literal[True]
    timeline_alignment_reviewed: Literal[True]
    labels_reviewed: Literal[True]
    greeting_delivery_reviewed: Literal[True]


def frozen_labels(plan: EntryPlan):
    data = {"cases": [{"id": c.id, "label": c.label} for c in plan.cases]}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def score_bundle(root: Path, bundle: PresenceBundle, *, profile):
    plan = EntryPlan.model_validate_json(read_bound(root, bundle.plan))
    start = BehaviorSnapshot.model_validate_json(read_bound(root, bundle.start))
    end = BehaviorSnapshot.model_validate_json(read_bound(root, bundle.end))
    review = PresenceReview.model_validate_json(read_bound(root, bundle.review))
    if review.profile != profile:
        raise ValueError("presence_profile_mismatch")
    for key in ("plan", "start", "end", "recording"):
        if getattr(review, key + "_sha256") != getattr(bundle, key).sha256:
            raise ValueError("presence_review_binding_mismatch")
    if review.frozen_labels_sha256 != frozen_labels(plan):
        raise ValueError("presence_frozen_labels_mismatch")
    if not review.frozen_at < review.recorded_at <= review.reviewed_at:
        raise ValueError("presence_review_chronology")
    if not review.observation_start < review.observation_end or any(
        c.start < review.observation_start or c.end > review.observation_end for c in plan.cases
    ):
        raise ValueError("presence_recorded_coverage_incomplete")
    if len(set(review.reviewed_case_ids)) != len(review.reviewed_case_ids) or set(
        review.reviewed_case_ids
    ) != {c.id for c in plan.cases}:
        raise ValueError("presence_case_review_incomplete")
    size = read_bound(root, bundle.recording, limit=512 * 1024 * 1024, retain=False)
    return {
        **score_observed_entries(plan, start, end, review.greetings),
        "artifacts": bundle.model_dump(),
        "recording_bytes": size,
        "declared_review_bound": True,
        "review_limitation": "Exact artifact binding and declared coverage/chronology only; independent source, detector uptime, labels and audible greeting review must be substantiated. Not physical qualification.",
    }
