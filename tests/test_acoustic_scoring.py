"""Synthetic PCM validates scoring math only; none of these are physical trials."""

import hashlib
import wave

import numpy as np
import pytest

from reachy_brain.evals.acoustics import CutoffTrial, measure_cutoff, summarize_cutoffs


def fixture(tmp_path, *, cutoff_ms=100, resumed_ms=None):
    rate, event = 16000, 16000
    pcm = np.zeros(4 * rate, dtype="<i2")
    pcm[event - 1600 : event + cutoff_ms * 16] = 8000
    if resumed_ms is not None:
        pcm[event + resumed_ms * 16 : event + resumed_ms * 16 + 1600] = 8000
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return CutoffTrial(
        id="synthetic",
        file=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        provider="openai",
        action="stop",
        channel=0,
        event_sample=event,
        uncertainty_ms=2.0,
        noise_start_sample=0,
        noise_end_sample=8000,
        threshold_dbfs=-40.0,
        independent_recording=True,
        human_reviewed_isolation=True,
        instrument="synthetic test signal, not a device",
        event_alignment="synthetic sample index",
    )


@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("ACOUSTIC-SCORER-CUTOFF-AND-RESUMPTION")
def test_last_energy_and_alignment_uncertainty_determine_cutoff(tmp_path):
    trial = fixture(tmp_path)
    measured = measure_cutoff(tmp_path, trial)
    assert measured["cutoff_upper_ms"] == 102
    assert summarize_cutoffs([measured])["openai/stop"]["passes_target"]
    trial = fixture(tmp_path, resumed_ms=800)
    measured = measure_cutoff(tmp_path, trial)
    assert measured["cutoff_upper_ms"] == 902
    assert not summarize_cutoffs([measured])["openai/stop"]["passes_target"]


@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("ACOUSTIC-HASHED-PCM-SNAPSHOT")
def test_replacement_after_hash_cannot_change_measured_pcm(tmp_path, monkeypatch):
    trial = fixture(tmp_path)
    original = wave.open

    def replace_before_decode(source, mode):
        (tmp_path / trial.file).write_bytes(b"replaced after input snapshot")
        return original(source, mode)

    monkeypatch.setattr("reachy_brain.evals.acoustics.wave.open", replace_before_decode)
    assert measure_cutoff(tmp_path, trial)["cutoff_upper_ms"] == 102


@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("ACOUSTIC-SCORER-REJECTS-INVALID-EVIDENCE")
@pytest.mark.parametrize(
    "updates,error",
    [
        ({"independent_recording": False}, "independent_isolated"),
        ({"human_reviewed_isolation": False}, "independent_isolated"),
        ({"sha256": "0" * 64}, "hash_mismatch"),
        ({"file": "../outside.wav"}, "outside_fixture_root"),
        ({"event_sample": 50000}, "insufficient_calibration_or_observation"),
        ({"event_sample": 12000}, "no_assistant_audio"),
        ({"noise_end_sample": 16000}, "insufficient_calibration_or_observation"),
    ],
)
def test_invalid_measurement_cannot_pass(tmp_path, updates, error):
    trial = fixture(tmp_path).model_copy(update=updates)
    with pytest.raises(ValueError, match=error):
        measure_cutoff(tmp_path, trial)


@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("ACOUSTIC-SCORER-PERCENTILES")
def test_percentiles_are_nearest_rank_and_provider_action_scoped():
    rows = [
        {"provider": "elevenlabs", "action": "spoken", "cutoff_upper_ms": n} for n in range(1, 21)
    ]
    rows.append({"provider": "openai", "action": "stop", "cutoff_upper_ms": 150})
    groups = summarize_cutoffs(rows)
    assert groups["elevenlabs/spoken"] == dict(
        count=20, median_ms=10.5, p95_ms=19, slowest_ms=20, target_ms=300, passes_target=True
    )
    assert not groups["openai/stop"]["passes_target"]
    assert "fallback/stop" not in groups


@pytest.mark.features("C2", "D5")
@pytest.mark.scenario("ACOUSTIC-SCORER-CALIBRATION-AND-TAIL")
def test_bad_noise_margin_and_unobserved_cutoff_cannot_pass(tmp_path):
    trial = fixture(tmp_path, cutoff_ms=3000)
    with pytest.raises(ValueError, match="cutoff_not_observed"):
        measure_cutoff(tmp_path, trial)
    trial = fixture(tmp_path)
    path = tmp_path / trial.file
    with wave.open(str(path), "rb") as source:
        pcm = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").copy()
    pcm[:8000] = 1000
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm.tobytes())
    trial.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="insufficient_noise_margin"):
        measure_cutoff(tmp_path, trial)
