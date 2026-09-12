"""Recorded JPEG sequences through production perception; replay time is not live timing."""

import hashlib
import queue
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reachy_brain.vision.events import PerceptionEvents
from reachy_brain.vision.worker import run_worker


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    file: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    at_ms: int = Field(ge=0, le=10000)


class RecordedClip(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    frames: list[Frame] = Field(min_length=11, max_length=151)

    @model_validator(mode="after")
    def timeline(self):
        times = [frame.at_ms for frame in self.frames]
        if times[0] != 0 or times[-1] < 2000:
            raise ValueError("clip_needs_zero_origin_and_two_seconds")
        if any(not 50 <= b - a <= 200 for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("clip_requires_ordered_5_to_20_fps_sampling")
        return self


class WaveClip(RecordedClip):
    label: Literal["wave", "negative"]
    category: Literal["wave", "static_hand", "drawing", "phone_use", "camera_motion", "other"]

    @model_validator(mode="after")
    def category_matches_label(self):
        if (self.label == "wave") != (self.category == "wave"):
            raise ValueError("clip_category_label_mismatch")
        return self


class WaveDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fixture_kind: Literal["real-prerecorded-camera"]
    operator: str = Field(min_length=1, max_length=128)
    frozen_at: str = Field(min_length=1, max_length=128)
    human_reviewed_labels: bool
    clips: list[WaveClip] = Field(min_length=40, max_length=200)

    @model_validator(mode="after")
    def coverage(self):
        if (
            not self.human_reviewed_labels
            or not self.operator.strip()
            or not self.frozen_at.strip()
        ):
            raise ValueError("reviewed_frozen_labels_required")
        if len({clip.id for clip in self.clips}) != len(self.clips):
            raise ValueError("duplicate_clip_ids")
        identities = {tuple((f.sha256, f.at_ms) for f in c.frames) for c in self.clips}
        if len(identities) != len(self.clips):
            raise ValueError("duplicate_recorded_sequences")
        if any(sum(c.label == label for c in self.clips) < 20 for label in ("wave", "negative")):
            raise ValueError("twenty_waves_and_twenty_negatives_required")
        if not {"static_hand", "drawing", "phone_use", "camera_motion"} <= {
            c.category for c in self.clips
        }:
            raise ValueError("negative_category_coverage_required")
        return self


def replay_clip(root: Path, clip: RecordedClip, models: Path, *, observation_consumer=None):
    root = root.resolve()
    frames = iter(clip.frames)
    state = SimpleNamespace(stopped=False, now=100.0, inputs=0, outputs=0, version=None)
    source = SimpleNamespace(id="recorded-camera", kind="camera", generation=1, enabled=True)
    interpreter = PerceptionEvents()
    waves, errors = [], []

    class Input:
        def get(self, timeout):
            try:
                frame = next(frames)
            except StopIteration:
                state.stopped = True
                raise queue.Empty() from None
            path = (root / frame.file).resolve()
            if not path.is_relative_to(root) or path == root:
                raise ValueError("clip_frame_outside_root")
            with path.open("rb") as handle:
                data = handle.read(1024 * 1024 + 1)
            if not data or len(data) > 1024 * 1024:
                raise ValueError("clip_frame_size_limit")
            if hashlib.sha256(data).hexdigest() != frame.sha256:
                raise ValueError("clip_frame_hash_mismatch")
            state.now = 100 + frame.at_ms / 1000
            state.inputs += 1
            return dict(source=source.id, generation=1, captured=state.now, jpeg=data)

    class Output:
        def put_nowait(self, result):
            if "error" in result:
                errors.append(result["error"])
                return
            state.outputs += 1
            state.version = result["detector"]
            events, observation = interpreter.update(result, source, now=state.now)
            if observation_consumer is not None:
                observation_consumer(observation, state.now, source)
            waves.extend(
                round((event.captured - 100) * 1000)
                for event in events
                if event.kind == "wave_detected"
            )

    run_worker(
        Input(),
        Output(),
        SimpleNamespace(is_set=lambda: state.stopped),
        str(models),
        clock=lambda: state.now,
    )
    if errors:
        raise ValueError("recorded_perception_error:" + errors[0])
    if state.outputs != len(clip.frames):
        raise ValueError("recorded_perception_incomplete_frames")
    return dict(
        id=clip.id,
        label=clip.label,
        category=clip.category,
        wave_events_ms=waves,
        frames=state.outputs,
        detector=state.version,
    )


def score_waves(rows):
    positives = [row for row in rows if row["label"] == "wave"]
    negatives = [row for row in rows if row["label"] == "negative"]
    hits = sum(bool(row["wave_events_ms"]) for row in positives)
    false_events = sum(len(row["wave_events_ms"]) for row in negatives)
    return dict(
        positives=len(positives),
        negatives=len(negatives),
        detected=hits,
        recall=hits / len(positives) if positives else 0,
        false_events=false_events,
        missed_ids=[row["id"] for row in positives if not row["wave_events_ms"]],
        false_event_ids=[row["id"] for row in negatives if row["wave_events_ms"]],
        passes_target=len(positives) >= 20
        and len(negatives) >= 20
        and hits * 10 >= len(positives) * 9
        and false_events <= 1,
    )
