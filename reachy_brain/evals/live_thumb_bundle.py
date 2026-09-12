"""Bind live thumb scoring to reviewed artifacts; hashes cannot authenticate physical origin."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .gesture_observation import GestureSnapshot, Identifier
from .live_thumbs import LiveThumbPlan, score_live_thumbs
from .robot_motion_bundle import Artifact, read_bound
from .screen_grounding import StrictRecord


class LiveThumbBundle(StrictRecord):
    plan: Artifact
    start: Artifact
    end: Artifact
    recording: Artifact
    review: Artifact


class LiveThumbReview(StrictRecord):
    profile: Literal["pc", "reachy_pc", "reachy_local"]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    end_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recording_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_at: float = Field(ge=0)
    recorded_at: float = Field(ge=0)
    reviewed_at: float = Field(ge=0)
    reviewer: Identifier
    source_kind: Literal["independent_live_camera_recording"]
    reviewed_case_ids: list[Identifier] = Field(min_length=60, max_length=200)
    live_source_reviewed: Literal[True]
    question_eligibility_reviewed: Literal[True]
    timeline_alignment_reviewed: Literal[True]
    feedback_reviewed: Literal[True]
    complete_observation_reviewed: Literal[True]


def label_digest(plan: LiveThumbPlan):
    # Runtime question/source IDs and interval alignment are assigned during capture;
    # expected classes and conditions must be frozen independently before that capture.
    rows = [
        {
            key: getattr(case, key)
            for key in ("id", "label", "category", "people", "orientation", "distance", "lighting")
        }
        for case in plan.cases
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def score_bundle(root: Path, bundle: LiveThumbBundle, *, profile):
    plan = LiveThumbPlan.model_validate_json(read_bound(root, bundle.plan))
    start = GestureSnapshot.model_validate_json(read_bound(root, bundle.start, limit=512 * 1024))
    end = GestureSnapshot.model_validate_json(read_bound(root, bundle.end, limit=512 * 1024))
    review = LiveThumbReview.model_validate_json(read_bound(root, bundle.review))
    if review.profile != profile or plan.profile != profile:
        raise ValueError("live_thumb_profile_mismatch")
    for key in ("plan", "start", "end", "recording"):
        if getattr(review, key + "_sha256") != getattr(bundle, key).sha256:
            raise ValueError("live_thumb_review_binding_mismatch")
    if review.frozen_labels_sha256 != label_digest(plan):
        raise ValueError("live_thumb_frozen_labels_mismatch")
    if not review.frozen_at < review.recorded_at <= review.reviewed_at:
        raise ValueError("live_thumb_review_chronology")
    if len(set(review.reviewed_case_ids)) != len(review.reviewed_case_ids) or set(
        review.reviewed_case_ids
    ) != {case.id for case in plan.cases}:
        raise ValueError("live_thumb_case_review_incomplete")
    recording_bytes = read_bound(root, bundle.recording, limit=512 * 1024 * 1024, retain=False)
    return {
        **score_live_thumbs(plan, start, end),
        "artifacts": bundle.model_dump(),
        "recording_bytes": recording_bytes,
        "declared_review_bound": True,
        "review_limitation": "Hash and chronology declarations require independent substantiation; no physical origin or complete product validation is inferred.",
    }
