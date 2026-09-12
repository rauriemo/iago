"""Mandatory prerecorded wave corpus; absence blocks this offline acceptance gate."""

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from reachy_brain.config import Settings
from reachy_brain.evals.wave_clips import WaveDataset, replay_clip, score_waves
from reachy_brain.vision.detectors import model_paths


@pytest.mark.features("P3")
@pytest.mark.scenario("WAVE-REAL-RECORDED-DATASET")
def test_real_recorded_wave_dataset(record_property):
    configured = os.environ.get("IAGO_WAVE_CLIP_MANIFEST")
    if not configured or not Path(configured).is_file():
        pytest.skip(
            "IAGO_WAVE_CLIP_MANIFEST required: >=20 real waves and >=20 real negatives; see docs/iago/WAVE_EVAL.md"
        )
    path = Path(configured).resolve()
    with path.open("rb") as source:
        data = source.read(5 * 1024 * 1024 + 1)
    assert len(data) <= 5 * 1024 * 1024, "wave manifest limit"
    dataset = WaveDataset.model_validate_json(data)
    models = Settings().perception_models
    if any(importlib.util.find_spec(name) is None for name in ("mediapipe", "cv2")):
        pytest.skip("Install the locked vision extra for recorded wave evaluation")
    try:
        model_paths(models)
    except FileNotFoundError:
        pytest.skip("Configured perception model assets are required for recorded wave evaluation")
    rows = []
    record_property("sample_count", len(dataset.clips))
    record_property(
        "expected", ">=90% wave recall and <=1 false event across the complete negative set"
    )
    try:
        for clip in dataset.clips:
            rows.append(replay_clip(path.parent, clip, models))
    finally:
        record_property(
            "measurements",
            dict(
                manifest_sha256=hashlib.sha256(data).hexdigest(),
                operator=dataset.operator,
                frozen_at=dataset.frozen_at,
                clips=rows,
                score=score_waves(rows),
                scope="Real prerecorded camera, operator-declared provenance; controlled replay time, not live camera or performance qualification",
            ),
        )
    assert len(rows) == len(dataset.clips) and score_waves(rows)["passes_target"]
