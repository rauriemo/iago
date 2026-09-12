"""Natural desk event accounting; reviewed declarations do not prove physical observation."""

from collections import Counter
from typing import Literal

from pydantic import Field

from .behavior_observation import BehaviorSnapshot, compare_decisions
from .gesture_observation import Identifier
from .screen_grounding import StrictRecord


class DeskEventReview(StrictRecord):
    event: Identifier
    source: Identifier
    generation: int = Field(ge=0)
    classification: Literal["correct", "false", "uncertain"]
    greeting_observed: bool


class DeskReview(StrictRecord):
    started: float = Field(ge=0)
    ended: float = Field(gt=0)
    natural_workload_reviewed: Literal[True]
    detector_continuity_reviewed: Literal[True]
    timeline_alignment_reviewed: Literal[True]
    events: list[DeskEventReview] = Field(max_length=10000)


def score_desk(snapshots: list[BehaviorSnapshot], review: DeskReview):
    if not 2 <= len(snapshots) <= 3602:
        raise ValueError("desk_snapshot_count")
    if review.ended - review.started < 1800:
        raise ValueError("thirty_minute_desk_observation_required")
    events = {}
    previous = snapshots[0]
    total_records = 0
    for snapshot in snapshots[1:]:
        interval = compare_decisions(previous, snapshot)
        previous = snapshot
        total_records += len(interval["records"])
        if total_records > 20000:
            raise ValueError("desk_record_limit")
        for row in interval["records"]:
            if row["kind"] not in {"person_entered_view", "wave_detected"}:
                continue
            key = (row["source"], row["generation"], row["event"])
            if key in events:
                old = events[key]
                if (old["kind"], old["captured"]) != (row["kind"], row["captured"]):
                    raise ValueError("desk_event_identity_changed")
                continue
            if row["decision"] in {"accepted", "duplicate"} or row["decision"].startswith(
                ("window_", "greeting_")
            ):
                raise ValueError("desk_initial_event_missing")
            if not review.started <= row["captured"] < review.ended:
                raise ValueError("desk_event_outside_review")
            events[key] = row
    labels = {(e.source, e.generation, e.event): e for e in review.events}
    if len(labels) != len(review.events) or set(labels) != set(events):
        raise ValueError("desk_event_review_coverage")
    counts = Counter((events[key]["kind"], label.classification) for key, label in labels.items())
    return {
        "declared_observation_seconds": review.ended - review.started,
        "event_count": len(events),
        "decision_record_count": total_records,
        "groups": {
            kind: {label: counts[kind, label] for label in ("correct", "false", "uncertain")}
            for kind in ("person_entered_view", "wave_detected")
        },
        "false_event_greetings": sum(
            e.classification == "false" and e.greeting_observed for e in labels.values()
        ),
        "review_complete": all(e.classification != "uncertain" for e in labels.values()),
        "physical_qualification": False,
        "scope": "Declared natural desk interval and reviewed event counts only. No new false-event pass threshold. Bound independent recordings, duration, detector uptime and greeting review remain required.",
    }
