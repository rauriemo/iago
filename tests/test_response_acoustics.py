"""Synthetic PCM checks scoring only, never physical latency qualification."""

import hashlib
import wave

import numpy as np
import pytest

from reachy_brain.evals.response_acoustics import (
    ResponseTrial,
    measure_response,
    summarize_responses,
)


def fixture(tmp_path, *, onset_ms=1250, audible=True):
    rate = 16000
    pcm = np.zeros(8 * rate, dtype="<i2")
    if audible:
        start = rate + onset_ms * 16
        pcm[start : start + rate] = 8000
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return ResponseTrial(
        id="synthetic",
        file=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provider="openai",
        workload="ordinary",
        channel=0,
        speech_end_sample=rate,
        uncertainty_ms=2.0,
        noise_start_sample=0,
        noise_end_sample=8000,
        threshold_dbfs=-40.0,
        independent_recording=True,
        human_reviewed_isolation=True,
        human_reviewed_turn=True,
        instrument="synthetic PCM",
        event_alignment="synthetic sample endpoint",
    )


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("RESPONSE-ACOUSTIC-ONSET")
def test_measured_onset_includes_window_and_alignment_uncertainty(tmp_path):
    trial = fixture(tmp_path)
    measured = measure_response(tmp_path, trial)
    assert measured["latency_upper_ms"] == 1257
    assert measured["recording_sha256"] == trial.sha256
    assert summarize_responses([measured])["openai/ordinary"]["passes_target"]


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("RESPONSE-ACOUSTIC-INVALID-EVIDENCE")
@pytest.mark.parametrize(
    "updates,error",
    [
        ({"independent_recording": False}, "independent_reviewed"),
        ({"human_reviewed_turn": False}, "independent_reviewed"),
        ({"sha256": "0" * 64}, "hash_mismatch"),
        ({"file": "../outside.wav"}, "outside_fixture_root"),
        ({"speech_end_sample": 127000}, "insufficient_calibration"),
        ({"speech_end_sample": 40000}, "assistant_audio_before_endpoint"),
    ],
)
def test_invalid_recording_cannot_pass(tmp_path, updates, error):
    trial = fixture(tmp_path).model_copy(update=updates)
    with pytest.raises(ValueError, match=error):
        measure_response(tmp_path, trial)


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("RESPONSE-ACOUSTIC-NO-OUTPUT")
def test_missing_audio_is_failure_not_zero_latency(tmp_path):
    trial = fixture(tmp_path, audible=False)
    with pytest.raises(ValueError, match="no_assistant_audio_after_endpoint"):
        measure_response(tmp_path, trial)


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("RESPONSE-ACOUSTIC-PERCENTILES")
def test_workload_provider_isolation_and_strict_thresholds():
    rows = [
        dict(provider="openai", workload="ordinary", latency_upper_ms=n * 100) for n in range(1, 21)
    ]
    rows += [
        dict(provider="elevenlabs", workload="ordinary", latency_upper_ms=3000),
        dict(provider="fallback", workload="ordinary", latency_upper_ms=5000),
        dict(provider="openai", workload="retrieval", latency_upper_ms=9000),
    ]
    groups = summarize_responses(rows)
    assert groups["openai/ordinary"]["count"] == 20
    assert groups["openai/ordinary"]["median_ms"] == 1050
    assert groups["openai/ordinary"]["p95_ms"] == 1900
    assert groups["openai/ordinary"]["slowest_ms"] == 2000
    assert groups["openai/ordinary"]["passes_target"]
    assert not groups["elevenlabs/ordinary"]["passes_target"]
    assert not groups["fallback/ordinary"]["passes_target"]
    assert groups["openai/retrieval"]["passes_target"] is None
    tail = [
        dict(provider="openai", workload="ordinary", latency_upper_ms=value)
        for value in ([1000] * 18 + [5000] * 2)
    ]
    summary = summarize_responses(tail)["openai/ordinary"]
    assert summary["median_ms"] == 1000 and summary["p95_ms"] == 5000
    assert not summary["passes_target"]


@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("RESPONSE-ACOUSTIC-CALIBRATION-AND-FORMAT")
@pytest.mark.parametrize(
    "case,error",
    [
        ("noise", "insufficient_noise_margin"),
        ("truncated", "truncated_recording"),
        ("channel", "unsupported_recording_format"),
    ],
)
def test_noise_and_incomplete_or_wrong_channel_recordings_fail(tmp_path, case, error):
    trial = fixture(tmp_path)
    path = tmp_path / trial.file
    if case == "noise":
        with wave.open(str(path), "rb") as source:
            pcm = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").copy()
        pcm[:8000] = 1000
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(pcm.tobytes())
    elif case == "truncated":
        path.write_bytes(path.read_bytes()[:-100])
    else:
        trial.channel = 1
    trial.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=error):
        measure_response(tmp_path, trial)
