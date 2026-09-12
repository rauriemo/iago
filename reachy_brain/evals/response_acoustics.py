"""Physical response onset from reviewed independent recordings, not server timestamps."""

import hashlib
import io
import math
import wave
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class ResponseTrial(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=128)
    file: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(pattern=r"^(openai|elevenlabs|fallback)$")
    workload: str = Field(pattern=r"^(ordinary|retrieval)$")
    channel: int = Field(ge=0, le=7)
    speech_end_sample: int = Field(ge=1)
    uncertainty_ms: float = Field(ge=0, le=50)
    noise_start_sample: int = Field(ge=0)
    noise_end_sample: int = Field(ge=1)
    threshold_dbfs: float = Field(ge=-80, le=-6)
    independent_recording: bool
    human_reviewed_isolation: bool
    human_reviewed_turn: bool
    instrument: str = Field(min_length=1, max_length=256)
    event_alignment: str = Field(min_length=1, max_length=512)


def measure_response(root: Path, trial: ResponseTrial):
    root = root.resolve()
    path = (root / trial.file).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("recording_outside_fixture_root")
    if not all(
        (trial.independent_recording, trial.human_reviewed_isolation, trial.human_reviewed_turn)
    ):
        raise ValueError("independent_reviewed_recording_required")
    # Hash and decode the same bounded bytes, even if the file changes concurrently.
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
        np.frombuffer(raw, dtype="<i2").reshape(-1, channels)[:, trial.channel].astype(float)
        / 32768
    )
    event = trial.speech_end_sample
    if not (
        0 <= trial.noise_start_sample < trial.noise_end_sample <= event - rate // 4
        and trial.noise_end_sample - trial.noise_start_sample >= rate // 4
        and event >= rate // 4
        and count - event >= rate * 5
    ):
        raise ValueError("insufficient_calibration_or_observation")
    threshold = 10 ** (trial.threshold_dbfs / 20)
    noise = pcm[trial.noise_start_sample : trial.noise_end_sample]
    if np.sqrt(np.mean(noise * noise)) > threshold / 4:
        raise ValueError("insufficient_noise_margin")
    window = max(1, round(rate * 0.005))

    def first_active(samples):
        for index in range(0, len(samples), window):
            block = samples[index : index + window]
            if np.sqrt(np.mean(block * block)) >= threshold:
                return index + len(block)
        return None

    if first_active(pcm[event - rate // 4 : event]) is not None:
        raise ValueError("assistant_audio_before_endpoint")
    onset = first_active(pcm[event:])
    if onset is None:
        raise ValueError("no_assistant_audio_after_endpoint")
    return dict(
        id=trial.id,
        provider=trial.provider,
        workload=trial.workload,
        latency_upper_ms=onset * 1000 / rate + trial.uncertainty_ms,
        alignment_uncertainty_ms=trial.uncertainty_ms,
        window_ms=window * 1000 / rate,
        recording_sha256=trial.sha256,
        rate=rate,
        observed_after_endpoint_ms=(count - event) * 1000 / rate,
    )


def summarize_responses(measurements):
    groups = {}
    for provider in ("openai", "elevenlabs", "fallback"):
        for workload in ("ordinary", "retrieval"):
            values = sorted(
                row["latency_upper_ms"]
                for row in measurements
                if row["provider"] == provider and row["workload"] == workload
            )
            if not values:
                continue
            median = float(np.median(values))
            p95 = values[math.ceil(len(values) * 0.95) - 1]
            groups[f"{provider}/{workload}"] = dict(
                count=len(values),
                median_ms=median,
                p95_ms=p95,
                slowest_ms=max(values),
                passes_target=(median < 3000 and p95 < 5000) if workload == "ordinary" else None,
            )
    return groups
