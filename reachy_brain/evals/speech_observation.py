"""Verify complete backend speech activity between exports; no physical echo inference."""

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from reachy_brain.evals.screen_grounding import StrictRecord, digest

Kind = Literal["onset", "uncertain_onset", "accepted_speech"]


class ActivityEvent(StrictRecord):
    sequence: int = Field(ge=1)
    kind: Kind
    epoch: int = Field(ge=0)
    seconds: float = Field(ge=0)


class ActivitySnapshot(StrictRecord):
    available: Literal[True] = True
    owner: str = Field(min_length=1, max_length=128)
    elapsed_seconds: float = Field(ge=0)
    counts: dict[Kind, int] = Field(min_length=3, max_length=3)
    samples: list[ActivityEvent] = Field(max_length=512)
    sample_limit: Literal[512]
    dropped_samples: int = Field(ge=0)
    scope: str = Field(max_length=2000)


def validate_snapshot(snapshot):
    if any(n < 0 for n in snapshot.counts.values()):
        raise ValueError("negative_activity_count")
    total = sum(snapshot.counts.values())
    if len(snapshot.samples) != min(total, 512) or snapshot.dropped_samples != total - len(
        snapshot.samples
    ):
        raise ValueError("inconsistent_activity_retention")
    previous_time = 0
    observed = Counter()
    for i, event in enumerate(snapshot.samples, snapshot.dropped_samples + 1):
        if event.sequence != i or not previous_time <= event.seconds <= snapshot.elapsed_seconds:
            raise ValueError("inconsistent_activity_sequence")
        previous_time = event.seconds
        observed[event.kind] += 1
    if any(observed[k] > n for k, n in snapshot.counts.items()):
        raise ValueError("inconsistent_activity_counts")
    return total


def compare_activity(start: ActivitySnapshot, end: ActivitySnapshot):
    first, last = validate_snapshot(start), validate_snapshot(end)
    if start.owner != end.owner:
        raise ValueError("activity_owner_changed")
    if end.elapsed_seconds <= start.elapsed_seconds or last < first:
        raise ValueError("activity_interval_reversed")
    if end.dropped_samples > first:
        raise ValueError("observation_events_dropped")
    old = {e.sequence: e for e in start.samples}
    for event in end.samples:
        if event.sequence in old and event != old[event.sequence]:
            raise ValueError("overlapping_activity_changed")
    events = [e for e in end.samples if e.sequence > first]
    if any(e.seconds < start.elapsed_seconds for e in events):
        raise ValueError("observation_time_mismatch")
    delta = {k: end.counts[k] - v for k, v in start.counts.items()}
    observed = Counter(e.kind for e in events)
    if any(n < 0 or observed[k] != n for k, n in delta.items()):
        raise ValueError("observation_count_mismatch")
    return {
        "start_sha256": digest(start),
        "end_sha256": digest(end),
        "backend_observation_seconds": end.elapsed_seconds - start.elapsed_seconds,
        "counts": delta,
        "events": [e.model_dump() for e in events],
        "event_coverage_complete": True,
        "physical_echo_validated": False,
        "limitation": "Complete only for backend notifications in this owner interval. Requires independent playback duration, microphone continuity and human classification; zero counts do not prove an echo pass.",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("start", "end", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    snapshots = []
    for path in (args.start, args.end):
        with path.open("rb") as stream:
            data = stream.read(512 * 1024 + 1)
        if len(data) > 512 * 1024:
            raise ValueError("activity_export_size_limit")
        snapshots.append(ActivitySnapshot.model_validate_json(data))
    result = compare_activity(*snapshots)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
