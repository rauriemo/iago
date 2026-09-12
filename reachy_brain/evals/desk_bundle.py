"""Bind natural desk observation to exact artifacts without authenticating declarations."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from .behavior_observation import BehaviorSnapshot
from .desk_observation import DeskReview, score_desk
from .gesture_observation import Identifier
from .robot_motion_bundle import Artifact, read_bound
from .screen_grounding import StrictRecord


class DeskBundle(StrictRecord):
    snapshots: Artifact
    recording: Artifact
    review: Artifact


class RecordedDeskReview(DeskReview):
    profile: Literal["pc", "reachy_pc", "reachy_local"]
    snapshots_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recording_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: Identifier
    recorded_at: float = Field(ge=0)
    reviewed_at: float = Field(ge=0)
    source_kind: Literal["independent_live_camera_recording"]
    complete_recording_reviewed: Literal[True]
    greeting_delivery_reviewed: Literal[True]


def score_bundle(root: Path, bundle: DeskBundle, *, profile):
    review = RecordedDeskReview.model_validate_json(read_bound(root, bundle.review))
    if review.profile != profile:
        raise ValueError("desk_profile_mismatch")
    if (
        review.snapshots_sha256 != bundle.snapshots.sha256
        or review.recording_sha256 != bundle.recording.sha256
    ):
        raise ValueError("desk_review_binding_mismatch")
    if review.reviewed_at < review.recorded_at:
        raise ValueError("desk_review_chronology")
    snapshots = TypeAdapter(
        Annotated[list[BehaviorSnapshot], Field(min_length=2, max_length=3602)]
    ).validate_json(read_bound(root, bundle.snapshots, limit=32 * 1024 * 1024))
    size = read_bound(root, bundle.recording, limit=512 * 1024 * 1024, retain=False)
    return {
        **score_desk(snapshots, review),
        "artifacts": bundle.model_dump(),
        "recording_bytes": size,
        "declared_review_bound": True,
        "review_limitation": "Recording/snapshot hashes and declared review only. Independent duration, live-source, detector-continuity and greeting evidence must be substantiated; no physical qualification.",
    }
