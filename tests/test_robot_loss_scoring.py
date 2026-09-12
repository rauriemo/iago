"""Synthetic PCM and loss traces test math/binding only, not physical PC loss."""

import hashlib

import pytest
from test_acoustic_scoring import fixture

from reachy_brain.evals.robot_loss import LossTrial, measure_loss, summarize_losses


@pytest.mark.features("C2", "D2", "D5")
@pytest.mark.scenario("ROBOT-LOSS-DISTRIBUTIONS")
def test_percentiles_cannot_hide_a_single_lease_overrun():
    rows = [
        {"provider": "openai", "action": "pc_pause", "cutoff_upper_ms": 100.0, "lease_seconds": 1.0}
        for _ in range(20)
    ]
    rows[-1]["cutoff_upper_ms"] = 1001.0
    rows.append(
        {
            "provider": "elevenlabs",
            "action": "pc_terminate",
            "cutoff_upper_ms": 250.0,
            "lease_seconds": 1.0,
        }
    )
    report = summarize_losses(rows)
    group = report["openai/pc_pause"]
    assert group == {
        "count": 20,
        "median_ms": 100.0,
        "p95_ms": 100.0,
        "slowest_ms": 1001.0,
        "lease_seconds": [1.0],
        "over_lease_count": 1,
    }
    assert report["elevenlabs/pc_terminate"]["count"] == 1
    assert report["elevenlabs/pc_terminate"]["over_lease_count"] == 0
    assert summarize_losses([]) == {}


@pytest.mark.features("C2", "D2", "D5")
@pytest.mark.scenario("ROBOT-LOSS-RECORDING-SCORER")
@pytest.mark.parametrize(
    "case", ["within", "late", "resumed", "unreviewed", "trace_changed", "short"]
)
def test_loss_bound_includes_uncertainty_and_late_resumption(tmp_path, case):
    recording = fixture(
        tmp_path,
        cutoff_ms=1001 if case == "late" else 100,
        resumed_ms=1200 if case == "resumed" else None,
    )
    trace = tmp_path / "synthetic-loss.txt"
    trace.write_bytes(b"synthetic loss; not physical evidence")
    trial = LossTrial(
        recording=recording,
        trigger="pc_pause",
        lease_seconds=1.0,
        trace_file=trace.name,
        trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
        human_reviewed_loss=True,
        loss_alignment="synthetic sample event",
    )
    if case == "unreviewed":
        trial.human_reviewed_loss = False
        error = "reviewed_pc_loss_required"
    elif case == "trace_changed":
        trace.write_bytes(b"changed")
        error = "loss_trace_hash_mismatch"
    elif case == "short":
        trial.lease_seconds = 3.0
        error = "lease_observation_too_short"
    else:
        result = measure_loss(tmp_path, trial)
        assert result["passes_lease_bound"] == (case == "within")
        assert result["action"] == "pc_pause"
        return
    with pytest.raises(ValueError, match=error):
        measure_loss(tmp_path, trial)
