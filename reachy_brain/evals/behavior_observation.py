"""Validate bounded behavior log continuity; decisions are not physical outcomes."""

from typing import Literal

from pydantic import Field

from .gesture_observation import Identifier
from .screen_grounding import StrictRecord, digest


class Decision(StrictRecord):
    sequence: int = Field(ge=1)
    event: Identifier
    source: Identifier
    generation: int = Field(ge=0)
    kind: Identifier
    captured: float = Field(ge=0)
    at: float = Field(ge=0)
    decision: Identifier


class BehaviorSnapshot(StrictRecord):
    owner: Identifier
    total: int = Field(ge=0)
    sample_limit: Literal[200]
    dropped_samples: int = Field(ge=0)
    samples: list[Decision] = Field(max_length=200)
    scope: str = Field(max_length=2000)


def validate(snapshot):
    if len(snapshot.samples) != min(
        snapshot.total, 200
    ) or snapshot.dropped_samples != snapshot.total - len(snapshot.samples):
        raise ValueError("behavior_retention_mismatch")
    if [r.sequence for r in snapshot.samples] != list(
        range(snapshot.dropped_samples + 1, snapshot.total + 1)
    ):
        raise ValueError("behavior_sequence_mismatch")


def compare_decisions(start: BehaviorSnapshot, end: BehaviorSnapshot):
    validate(start)
    validate(end)
    if start.owner != end.owner or end.total < start.total:
        raise ValueError("behavior_owner_or_order_changed")
    if end.dropped_samples > start.total:
        raise ValueError("behavior_interval_records_dropped")
    old = {r.sequence: r for r in start.samples}
    for row in end.samples:
        if row.sequence in old and row != old[row.sequence]:
            raise ValueError("behavior_overlap_changed")
    rows = [r.model_dump() for r in end.samples if r.sequence > start.total]
    identities = {}
    for row in rows:
        key = (row["source"], row["generation"], row["event"])
        identity = (row["kind"], row["captured"])
        if key in identities and identities[key] != identity:
            raise ValueError("behavior_event_identity_changed")
        identities[key] = identity
    return {
        "start_sha256": digest(start),
        "end_sha256": digest(end),
        "records": rows,
        "record_coverage_complete": True,
        "physical_qualification": False,
        "scope": "Complete log sequence interval only; wall-clock alignment, detector continuity and delivered actions need independent evidence.",
    }
