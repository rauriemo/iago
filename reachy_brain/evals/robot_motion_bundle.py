"""Bounded motion evidence binding; human review does not become sensor proof."""

import hashlib
import json
from pathlib import Path

from pydantic import Field

from .robot_motion import CHANNELS, MotionTrace, score_motion
from .screen_grounding import StrictRecord


class Artifact(StrictRecord):
    file: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MotionBundle(StrictRecord):
    trace: Artifact
    calibration: Artifact
    review: Artifact
    recording: Artifact


def read_bound(root: Path, artifact: Artifact, *, limit=2 * 1024 * 1024, retain=True):
    root = root.resolve()
    path = (root / artifact.file).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("motion_artifact_outside_root")
    digest = hashlib.sha256()
    chunks = []
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(min(1024 * 1024, limit - size + 1)):
            size += len(chunk)
            if size > limit:
                raise ValueError("motion_artifact_size_limit")
            digest.update(chunk)
            if retain:
                chunks.append(chunk)
    if not size or digest.hexdigest() != artifact.sha256:
        raise ValueError("motion_artifact_hash_mismatch")
    return b"".join(chunks) if retain else size


def score_bundle(root: Path, bundle: MotionBundle, *, expected_trigger=None, expected_profile=None):
    trace = MotionTrace.model_validate_json(read_bound(root, bundle.trace))
    calibration = json.loads(read_bound(root, bundle.calibration))
    review = json.loads(read_bound(root, bundle.review))
    if not isinstance(calibration, dict) or not isinstance(review, dict):
        raise ValueError("motion_record_object_required")
    if (
        calibration.get("channels") != list(CHANNELS)
        or calibration.get("measurement_error") != trace.measurement_error
    ):
        raise ValueError("motion_calibration_mismatch")
    frozen, recorded = calibration.get("frozen_at"), review.get("recorded_at")
    import math

    if (
        not all(type(v) in (int, float) and math.isfinite(v) for v in (frozen, recorded))
        or not 0 <= frozen < recorded
    ):
        raise ValueError("motion_calibration_chronology")
    for key, artifact in (
        ("trace_sha256", bundle.trace),
        ("calibration_sha256", bundle.calibration),
        ("recording_sha256", bundle.recording),
    ):
        if review.get(key) != artifact.sha256:
            raise ValueError("motion_review_binding_mismatch")
    if (
        review.get("source_kind") != "independent_pose_recording"
        or review.get("calibration_reviewed") is not True
        or review.get("event_alignment_reviewed") is not True
        or review.get("pose_extraction_reviewed") is not True
        or not isinstance(review.get("reviewer"), str)
        or not 0 < len(review["reviewer"].strip()) <= 256
    ):
        raise ValueError("independent_motion_review_required")
    if expected_trigger is not None and review.get("trigger") != expected_trigger:
        raise ValueError("motion_trigger_binding_mismatch")
    if expected_profile is not None and review.get("profile") != expected_profile:
        raise ValueError("motion_profile_binding_mismatch")
    recording_bytes = read_bound(root, bundle.recording, limit=512 * 1024 * 1024, retain=False)
    return {
        **score_motion(trace),
        "artifacts": bundle.model_dump(),
        "recording_bytes": recording_bytes,
        "declared_review_bound": True,
        "scope": "Hash-bound trace/calibration/independent-recording review; declarations and hashes do not independently prove origin, calibration quality or physical hold.",
    }
