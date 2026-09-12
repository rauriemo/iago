"""Mandatory real thumb recordings, scored at accepted controller responses."""

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from reachy_brain.config import Settings
from reachy_brain.evals.thumb_clips import ThumbDataset, replay_thumbs, score_thumbs
from reachy_brain.vision.detectors import model_paths


@pytest.mark.features("P10")
@pytest.mark.scenario("THUMBS-REAL-RECORDED-DATASET")
def test_real_recorded_thumb_dataset(record_property):
    configured = os.environ.get("IAGO_THUMB_CLIP_MANIFEST")
    if not configured or not Path(configured).is_file():
        pytest.skip(
            "IAGO_THUMB_CLIP_MANIFEST required: >=20 real up/down/negative clips each; see docs/iago/THUMB_EVAL.md"
        )
    path = Path(configured).resolve()
    with path.open("rb") as source:
        data = source.read(5 * 1024 * 1024 + 1)
    assert len(data) <= 5 * 1024 * 1024, "thumb manifest limit"
    dataset = ThumbDataset.model_validate_json(data)
    models = Settings().perception_models
    if any(importlib.util.find_spec(name) is None for name in ("mediapipe", "cv2")):
        pytest.skip("Install the locked vision extra for recorded thumb evaluation")
    try:
        model_paths(models)
    except FileNotFoundError:
        pytest.skip("Configured perception models required for recorded thumb evaluation")
    rows = []
    record_property("sample_count", len(dataset.clips))
    record_property(
        "expected",
        ">=90% accepted up/down responses each, <=1 negative false response, zero wrong polarity, duplicates or invalid question/person context",
    )
    try:
        for clip in dataset.clips:
            rows.append(replay_thumbs(path.parent, clip, models))
    finally:
        record_property(
            "measurements",
            dict(
                manifest_sha256=hashlib.sha256(data).hexdigest(),
                operator=dataset.operator,
                frozen_at=dataset.frozen_at,
                clips=rows,
                score=score_thumbs(rows),
                scope="Recorded camera through production detector/interpreter/ThumbController; controlled question timeline, not live webcam/UI/Astra/robot qualification",
            ),
        )
    assert len(rows) == len(dataset.clips) and score_thumbs(rows)["passes_target"]
