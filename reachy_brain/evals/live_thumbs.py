"""Score labeled live controller intervals; independent physical provenance remains required."""

from typing import Literal

from pydantic import Field, model_validator

from .gesture_observation import GestureSnapshot, Identifier, compare_gestures
from .screen_grounding import StrictRecord, digest
from .thumb_clips import NEGATIVES


class LiveThumbCase(StrictRecord):
    id: Identifier
    label: Literal["thumb_up", "thumb_down", "negative"]
    category: Identifier
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    source: Identifier
    generation: int = Field(ge=0)
    session: Identifier
    question: Identifier | None
    turn: int = Field(ge=0)
    people: int = Field(ge=0, le=8)
    orientation: Identifier
    distance: Identifier
    lighting: Identifier

    @model_validator(mode="after")
    def valid_case(self):
        if self.end <= self.start:
            raise ValueError("invalid_live_thumb_interval")
        if self.label == "negative":
            if self.category not in NEGATIVES:
                raise ValueError("negative_category_required")
        elif self.category != self.label or self.people != 1 or self.question is None:
            raise ValueError("positive_thumb_context_required")
        return self


class LiveThumbPlan(StrictRecord):
    profile: Literal["pc", "reachy_pc", "reachy_local"]
    owner: Identifier
    cases: list[LiveThumbCase] = Field(min_length=60, max_length=200)

    @model_validator(mode="after")
    def coverage(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate_case_ids")
        ordered = sorted(self.cases, key=lambda c: c.start)
        if any(a.end > b.start for a, b in zip(ordered, ordered[1:], strict=False)):
            raise ValueError("overlapping_case_intervals")
        for label in ("thumb_up", "thumb_down", "negative"):
            subset = [c for c in self.cases if c.label == label]
            if len(subset) < 20:
                raise ValueError("twenty_per_class_required")
            if label != "negative":
                for field in ("orientation", "distance", "lighting"):
                    if len({getattr(c, field).strip() for c in subset} - {""}) < 2:
                        raise ValueError("varied_positive_conditions_required")
        if not NEGATIVES <= {c.category for c in self.cases if c.label == "negative"}:
            raise ValueError("negative_category_coverage_required")
        return self


def score_live_thumbs(plan: LiveThumbPlan, start: GestureSnapshot, end: GestureSnapshot):
    observation = compare_gestures(start, end)
    if start.owner != plan.owner:
        raise ValueError("live_thumb_owner_mismatch")
    if any(c.start < start.elapsed_seconds or c.end > end.elapsed_seconds for c in plan.cases):
        raise ValueError("live_thumb_interval_not_observed")
    accepted = [e for e in observation["events"] if e["kind"] == "accepted"]
    if len({e["slot"] for e in accepted}) != len(accepted):
        raise ValueError("duplicate_accepted_slot")
    assigned = set()
    results = []
    wrong = invalid = duplicates = false = 0
    for case in plan.cases:
        events = [e for e in accepted if case.start <= e["seconds"] < case.end]
        assigned.update(e["sequence"] for e in events)
        context_errors = sum(
            case.people != 1
            or case.question is None
            or any(
                e[k] != getattr(case, k)
                for k in ("source", "generation", "session", "question", "turn")
            )
            for e in events
        )
        invalid += context_errors
        duplicates += max(0, len(events) - 1)
        expected = "yes" if case.label == "thumb_up" else "no"
        polarity_errors = (
            sum(e["value"] != expected or e["gesture"] != case.label for e in events)
            if case.label != "negative"
            else 0
        )
        wrong += polarity_errors
        false += len(events) if case.label == "negative" else 0
        results.append(
            {
                "id": case.id,
                "label": case.label,
                "accepted": events,
                "correct": len(events) == 1 and not context_errors and not polarity_errors
                if case.label != "negative"
                else not events,
            }
        )
    if len(assigned) != len(accepted):
        raise ValueError("unassigned_live_thumb_acceptance")
    groups = {}
    for label in ("thumb_up", "thumb_down"):
        rows = [r for r in results if r["label"] == label]
        correct = sum(r["correct"] for r in rows)
        groups[label] = {
            "count": len(rows),
            "correct": correct,
            "recall": correct / len(rows),
            "missed_ids": [r["id"] for r in rows if not r["correct"]],
        }
    return {
        "plan_sha256": digest(plan),
        "observation": observation,
        "cases": results,
        "groups": groups,
        "false_responses": false,
        "wrong_polarity": wrong,
        "invalid_context_responses": invalid,
        "duplicate_responses": duplicates,
        "passes_numeric_targets": false <= 1
        and wrong == invalid == duplicates == 0
        and all(g["correct"] * 10 >= g["count"] * 9 for g in groups.values()),
        "physical_gesture_validated": False,
        "release_validated": False,
        "scope": "Initial controller acceptance counts only. Supersessions retained separately. Labels, live source origin, question eligibility, feedback, and timing-to-recording alignment need independent bound review.",
    }
