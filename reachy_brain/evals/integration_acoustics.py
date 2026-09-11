"""Score recorded cutoff trials during independently reviewed blocked integration work."""

import hashlib
import math
from pathlib import Path
from typing import Literal

from pydantic import Field

from .acoustics import CutoffTrial, measure_cutoff, summarize_cutoffs

ACTIVITIES = ("mcp_read", "mcp_write", "credential_refresh", "skill_load", "reconciliation")


class IntegrationCutoffTrial(CutoffTrial):
    activity: Literal["mcp_read", "mcp_write", "credential_refresh", "skill_load", "reconciliation"]
    activity_started_sample: int = Field(ge=0)
    activity_released_sample: int = Field(ge=1)
    activity_trace_file: str = Field(min_length=1, max_length=256)
    activity_trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_reviewed_activity: bool
    activity_alignment: str = Field(min_length=1, max_length=1024)


def validate_trial_identity(trials):
    if not 1 <= len(trials) <= 1000:
        raise ValueError("integration_trial_count_limit")
    if len({trial.id for trial in trials}) != len(trials):
        raise ValueError("duplicate_trial_id")
    # Another channel from the same event is not a separate voice/activity trial.
    if len({(trial.sha256, trial.event_sample) for trial in trials}) != len(trials):
        raise ValueError("duplicate_recorded_event")


def measure_integration_cutoff(root: Path, trial: IntegrationCutoffTrial):
    if not trial.human_reviewed_activity or not trial.activity_alignment.strip():
        raise ValueError("reviewed_activity_alignment_required")
    root = root.resolve()
    trace = (root / trial.activity_trace_file).resolve()
    if not trace.is_relative_to(root) or trace == root:
        raise ValueError("activity_trace_outside_fixture_root")
    with trace.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    if not data or len(data) > 1024 * 1024:
        raise ValueError("activity_trace_size_limit")
    if hashlib.sha256(data).hexdigest() != trial.activity_trace_sha256:
        raise ValueError("activity_trace_hash_mismatch")
    measured = measure_cutoff(root, trial)
    rate = measured["rate"]
    margin = math.ceil(trial.uncertainty_ms * rate / 1000)
    # Activity must already be blocked at the earliest possible event, and remain
    # blocked through measured cutoff plus an observed half-second silence tail.
    required_end = trial.event_sample + math.ceil((measured["cutoff_upper_ms"] + 500) * rate / 1000)
    observation_end = trial.event_sample + round(measured["observed_after_event_ms"] * rate / 1000)
    if not (
        trial.activity_started_sample < trial.event_sample - margin
        and required_end <= trial.activity_released_sample <= observation_end
    ):
        raise ValueError("activity_does_not_span_interruption_and_silence")
    return {
        **measured,
        "activity": trial.activity,
        "activity_trace_sha256": trial.activity_trace_sha256,
        "activity_started_sample": trial.activity_started_sample,
        "activity_released_sample": trial.activity_released_sample,
        "activity_alignment": trial.activity_alignment,
        "activity_provenance": "operator reviewed; trace hash binds bytes, not authenticity",
    }


def summarize_integration_cutoffs(measurements):
    groups = {}
    for activity in ACTIVITIES:
        subset = [row for row in measurements if row["activity"] == activity]
        for key, value in summarize_cutoffs(subset).items():
            groups[f"{activity}/{key}"] = value
    required = {
        f"{activity}/{provider}/{action}"
        for activity in ACTIVITIES
        for provider in ("openai", "elevenlabs", "fallback")
        for action in ("stop", "spoken")
    }
    missing = sorted(required - groups.keys())
    spoken_count = sum(row["action"] == "spoken" for row in measurements)
    return {
        "groups": groups,
        "missing_groups": missing,
        "spoken_count": spoken_count,
        "passes_target": not missing
        and spoken_count >= 20
        and all(group["passes_target"] for group in groups.values()),
    }
