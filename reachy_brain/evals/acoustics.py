"""Cutoff measurements from independently recorded PCM, not cancellation acknowledgments."""

import hashlib
import io
import math
import wave
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class CutoffTrial(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=128)
    file: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(pattern=r"^(openai|elevenlabs|fallback)$")
    action: str = Field(pattern=r"^(stop|spoken)$")
    channel: int = Field(ge=0, le=7)
    event_sample: int = Field(ge=1)
    uncertainty_ms: float = Field(ge=0, le=50)
    noise_start_sample: int = Field(ge=0)
    noise_end_sample: int = Field(ge=1)
    # Fixed before scoring from reviewed room/channel calibration, not tuned to a cutoff.
    threshold_dbfs: float = Field(ge=-80, le=-6)
    independent_recording: bool
    human_reviewed_isolation: bool
    instrument: str = Field(min_length=1, max_length=256)
    event_alignment: str = Field(min_length=1, max_length=512)


def measure_cutoff(root: Path, trial: CutoffTrial):
    root = root.resolve()
    path = (root / trial.file).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("recording_outside_fixture_root")
    if not trial.independent_recording or not trial.human_reviewed_isolation:
        raise ValueError("independent_isolated_recording_required")
    with path.open("rb") as source:
        data = source.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("recording_size_limit")
    if hashlib.sha256(data).hexdigest() != trial.sha256:
        raise ValueError("recording_hash_mismatch")
    with wave.open(io.BytesIO(data), "rb") as source:
        rate, channels, count = source.getframerate(), source.getnchannels(), source.getnframes()
        if (
            source.getsampwidth() != 2
            or source.getcomptype() != "NONE"
            or not 8000 <= rate <= 96000
            or not 1 <= channels <= 8
            or trial.channel >= channels
            or count > rate * 30
        ):
            raise ValueError("unsupported_recording_format")
        raw = source.readframes(count)
    if len(raw) != count * channels * 2:
        raise ValueError("truncated_recording")
    pcm = (
        np.frombuffer(raw, dtype="<i2").reshape(-1, channels)[:, trial.channel].astype(np.float64)
        / 32768
    )
    event = trial.event_sample
    if not (
        0 <= trial.noise_start_sample < trial.noise_end_sample <= event - rate // 10
        and trial.noise_end_sample - trial.noise_start_sample >= rate // 4
        and event >= rate // 10
        and count - event >= rate * 2
    ):
        raise ValueError("insufficient_calibration_or_observation")
    threshold = 10 ** (trial.threshold_dbfs / 20)
    noise = pcm[trial.noise_start_sample : trial.noise_end_sample]
    if np.sqrt(np.mean(noise * noise)) > threshold / 4:
        raise ValueError("insufficient_noise_margin")
    window = max(1, round(rate * 0.005))

    def active_windows(samples):
        return [
            index + len(block)
            for index in range(0, len(samples), window)
            if (
                len(block := samples[index : index + window])
                and np.sqrt(np.mean(block * block)) >= threshold
            )
        ]

    if not active_windows(pcm[event - rate // 10 : event]):
        raise ValueError("no_assistant_audio_before_event")
    active = active_windows(pcm[event:])
    last = active[-1] if active else 0
    if count - event - last < rate // 2:
        raise ValueError("cutoff_not_observed_with_silence_tail")
    # Last audible energy includes resumed output and uncertainty, not first short silence.
    upper_ms = last * 1000 / rate + trial.uncertainty_ms
    return {
        "id": trial.id,
        "provider": trial.provider,
        "action": trial.action,
        "cutoff_upper_ms": upper_ms,
        "alignment_uncertainty_ms": trial.uncertainty_ms,
        "window_ms": window * 1000 / rate,
        "recording_sha256": trial.sha256,
        "rate": rate,
        "observed_after_event_ms": (count - event) * 1000 / rate,
    }


def summarize_cutoffs(measurements):
    groups = {}
    for provider in ("openai", "elevenlabs", "fallback"):
        for action, target in (("stop", 150), ("spoken", 300)):
            values = sorted(
                row["cutoff_upper_ms"]
                for row in measurements
                if row["provider"] == provider and row["action"] == action
            )
            if not values:
                continue
            p95 = values[math.ceil(len(values) * 0.95) - 1]
            groups[f"{provider}/{action}"] = {
                "count": len(values),
                "median_ms": float(np.median(values)),
                "p95_ms": p95,
                "slowest_ms": max(values),
                "target_ms": target,
                "passes_target": p95 < target,
            }
    return groups
