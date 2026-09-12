"""Synthetic JPEG/model outputs exercise replay and scoring, not real wave accuracy."""

import hashlib
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from reachy_brain.evals.wave_clips import WaveClip, WaveDataset, replay_clip, score_waves


def clip_fixture(tmp_path):
    path = tmp_path / "synthetic.jpg"
    Image.new("RGB", (320, 240), "black").save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return WaveClip(
        id="synthetic",
        label="negative",
        category="static_hand",
        frames=[dict(file=path.name, sha256=digest, at_ms=i * 100) for i in range(31)],
    )


@pytest.mark.features("P3")
@pytest.mark.scenario("WAVE-REPLAY-REAL-MODEL-SYNTHETIC-IMAGE")
def test_blank_recording_runs_actual_worker_and_models(tmp_path):
    clip = clip_fixture(tmp_path)
    result = replay_clip(tmp_path, clip, Path("local-data/models"))
    assert result["frames"] == 31 and result["wave_events_ms"] == []
    assert result["detector"].startswith("mediapipe-")


@pytest.mark.features("P3")
@pytest.mark.scenario("WAVE-REPLAY-REJECTS-INVALID-FRAMES")
@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("file", "../outside.jpg")])
def test_invalid_frame_cannot_supply_a_negative_pass(tmp_path, field, value):
    clip = clip_fixture(tmp_path)
    clip.frames[0] = clip.frames[0].model_copy(update={field: value})
    with pytest.raises(ValueError, match="recorded_perception_error"):
        replay_clip(tmp_path, clip, Path("local-data/models"))


@pytest.mark.features("P3")
@pytest.mark.scenario("WAVE-REPLAY-EVENT-TIMELINE")
def test_synthetic_detector_tracks_exercise_actual_temporal_interpreter(tmp_path, monkeypatch):
    class SyntheticDetectors:
        version = "synthetic-tracks-not-real-media"

        def __init__(self, models):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def objects(self, rgb, timestamp):
            return [dict(label="person", confidence=0.99, box=[0, 0, 1, 1])]

        def hands(self, rgb, timestamp):
            position = [0.3, 0.35, 0.4, 0.45, 0.5, 0.45, 0.4, 0.35][(timestamp // 100) % 8]
            landmarks = [[position, 0.5, 0] for _ in range(21)]
            landmarks[9] = [position, 0.6, 0]
            return [dict(gesture="Open_Palm", confidence=0.99, side="Right", landmarks=landmarks)]

    monkeypatch.setattr("reachy_brain.vision.worker.LocalDetectors", SyntheticDetectors)
    row = replay_clip(tmp_path, clip_fixture(tmp_path), tmp_path)
    assert len(row["wave_events_ms"]) == 1
    assert 600 <= row["wave_events_ms"][0] <= 3000


@pytest.mark.features("P3")
@pytest.mark.scenario("WAVE-REPLAY-FROZEN-DATASET-VALIDATION")
def test_incomplete_duplicate_or_unreviewed_dataset_cannot_qualify(tmp_path):
    clip = clip_fixture(tmp_path).model_dump()
    data = dict(
        fixture_kind="real-prerecorded-camera",
        operator="synthetic-test-only",
        frozen_at="test",
        human_reviewed_labels=True,
        clips=[dict(clip, id=f"clip-{i}") for i in range(40)],
    )
    with pytest.raises(ValidationError, match="duplicate_recorded_sequences"):
        WaveDataset.model_validate(data)
    data["human_reviewed_labels"] = False
    with pytest.raises(ValidationError, match="reviewed_frozen"):
        WaveDataset.model_validate(data)
    clip["frames"][1]["at_ms"] = 0
    with pytest.raises(ValidationError, match="ordered"):
        WaveClip.model_validate(clip)


@pytest.mark.features("P3")
@pytest.mark.scenario("WAVE-REPLAY-SCORING-THRESHOLDS")
def test_thresholds_count_false_events_and_preserve_recall():
    rows = [
        dict(
            id=str(i),
            label="wave" if i < 20 else "negative",
            wave_events_ms=[1000] if i < 18 else [],
        )
        for i in range(40)
    ]
    assert score_waves(rows)["passes_target"]
    rows[20]["wave_events_ms"] = [1000]
    assert score_waves(rows)["passes_target"]
    rows[20]["wave_events_ms"] = [1000, 2000]
    assert not score_waves(rows)["passes_target"]
    rows[20]["wave_events_ms"] = []
    rows[0]["wave_events_ms"] = []
    assert not score_waves(rows)["passes_target"]
    assert score_waves(rows)["missed_ids"] == ["0", "18", "19"]
