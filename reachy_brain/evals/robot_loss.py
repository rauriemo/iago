"""Independent robot audio after PC loss; no motion or recovery qualification."""

import hashlib
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import Field

from .acoustics import CutoffTrial, measure_cutoff
from .screen_grounding import StrictRecord


def summarize_losses(measurements):
    """Describe each measured group; never turn a percentile into a lease pass."""
    grouped = defaultdict(list)
    for row in measurements:
        grouped[(row["provider"], row["action"])].append(row)
    result = {}
    for (provider, trigger), rows in sorted(grouped.items()):
        values = sorted(row["cutoff_upper_ms"] for row in rows)
        result[f"{provider}/{trigger}"] = {
            "count": len(values),
            "median_ms": statistics.median(values),
            "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
            "slowest_ms": values[-1],
            "lease_seconds": sorted({row["lease_seconds"] for row in rows}),
            "over_lease_count": sum(
                row["cutoff_upper_ms"] > row["lease_seconds"] * 1000 for row in rows
            ),
        }
    return result


class LossTrial(StrictRecord):
    recording: CutoffTrial
    trigger: Literal["pc_pause", "pc_terminate"]
    lease_seconds: float = Field(gt=0, le=10)
    trace_file: str = Field(min_length=1, max_length=256)
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_reviewed_loss: bool
    loss_alignment: str = Field(min_length=1, max_length=1024)


def measure_loss(root: Path, trial: LossTrial):
    # The shared primitive measures last audio after event_sample. Here that
    # sample denotes reviewed PC loss, never an application Stop command.
    if (
        trial.recording.action != "stop"
        or not trial.human_reviewed_loss
        or not trial.loss_alignment.strip()
    ):
        raise ValueError("reviewed_pc_loss_required")
    root = root.resolve()
    trace = (root / trial.trace_file).resolve()
    if not trace.is_relative_to(root) or trace == root:
        raise ValueError("loss_trace_outside_root")
    with trace.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    if not data or len(data) > 1024 * 1024:
        raise ValueError("loss_trace_size_limit")
    if hashlib.sha256(data).hexdigest() != trial.trace_sha256:
        raise ValueError("loss_trace_hash_mismatch")
    measured = measure_cutoff(root, trial.recording)
    if measured["observed_after_event_ms"] < trial.lease_seconds * 1000 + 500:
        raise ValueError("lease_observation_too_short")
    return {
        **measured,
        "action": trial.trigger,
        "lease_seconds": trial.lease_seconds,
        "trace_sha256": trial.trace_sha256,
        "passes_lease_bound": measured["cutoff_upper_ms"] <= trial.lease_seconds * 1000,
        "scope": "Reviewed recorded audio after PC loss only; trace authenticity, motion stop, stale output after reconnection and recovery require separate evidence",
    }
