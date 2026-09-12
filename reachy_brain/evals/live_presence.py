"""Score independently labeled entry intervals; records alone cannot prove live origin."""

from typing import Literal

from pydantic import Field, model_validator

from .behavior_observation import BehaviorSnapshot, compare_decisions
from .gesture_observation import Identifier
from .screen_grounding import StrictRecord


class EntryCase(StrictRecord):
    id: Identifier
    label: Literal["entry", "startup", "occlusion"]
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    source: Identifier
    generation: int = Field(ge=0)


class EntryPlan(StrictRecord):
    cases: list[EntryCase] = Field(min_length=12, max_length=200)

    @model_validator(mode="after")
    def coverage(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate_entry_cases")
        if sum(c.label == "entry" for c in self.cases) < 10 or not {"startup", "occlusion"} <= {
            c.label for c in self.cases
        }:
            raise ValueError("entry_scenario_coverage_required")
        ordered = sorted(self.cases, key=lambda c: c.start)
        if any(c.end <= c.start for c in ordered) or any(
            a.end > b.start for a, b in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError("invalid_entry_intervals")
        return self


class EntryDetection(StrictRecord):
    id: Identifier
    source: Identifier
    generation: int = Field(ge=0)
    at: float = Field(ge=0)
    greeted: bool


class GreetingReview(StrictRecord):
    id: Identifier
    source: Identifier
    generation: int = Field(ge=0)
    greeted: bool


def score_observed_entries(
    plan: EntryPlan, start: BehaviorSnapshot, end: BehaviorSnapshot, reviews: list[GreetingReview]
):
    observation = compare_decisions(start, end)
    if len(reviews) > 1000:
        raise ValueError("entry_review_limit")
    indexed = {(r.source, r.generation, r.id): r for r in reviews}
    if len(indexed) != len(reviews):
        raise ValueError("duplicate_greeting_review")
    events = {}
    for row in observation["records"]:
        if row["kind"] != "person_entered_view":
            continue
        key = (row["source"], row["generation"], row["event"])
        if key not in events:
            if (
                row["decision"] == "duplicate"
                or row["decision"] == "accepted"
                or row["decision"].startswith(("window_", "greeting_"))
            ):
                raise ValueError("entry_initial_observation_missing")
            events[key] = row
    if set(indexed) != set(events):
        raise ValueError("entry_greeting_review_coverage")
    detections = [
        EntryDetection(
            id=row["event"],
            source=row["source"],
            generation=row["generation"],
            at=row["captured"],
            greeted=indexed[key].greeted,
        )
        for key, row in events.items()
    ]
    return {
        **score_entries(plan, detections),
        "observation": observation,
        "review_limitation": "Greeting judgments must be independently bound to complete live recording; scheduled work is not a reviewed audible greeting.",
    }


def score_entries(plan: EntryPlan, detections: list[EntryDetection]):
    if len(detections) > 1000:
        raise ValueError("entry_detection_limit")
    keys = [(d.source, d.generation, d.id) for d in detections]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate_entry_observations")
    rows, assigned = [], set()
    for case in plan.cases:
        events = [d for d in detections if case.start <= d.at < case.end]
        assigned.update((d.source, d.generation, d.id) for d in events)
        wrong_source = sum(
            d.source != case.source or d.generation != case.generation for d in events
        )
        count_ok = (
            len(events) == 1
            if case.label == "entry"
            else len(events) <= 1
            if case.label == "startup"
            else not events
        )
        greetings = sum(d.greeted for d in events)
        rows.append(
            {
                "id": case.id,
                "label": case.label,
                "count": len(events),
                "event_ids": [d.id for d in events],
                "wrong_source_count": wrong_source,
                "greeting_count": greetings,
                "passes_target": count_ok
                and wrong_source == 0
                and (case.label == "entry" or greetings == 0),
            }
        )
    unassigned = len(detections) - len(assigned)
    return {
        "cases": rows,
        "unassigned_events": unassigned,
        "passes_numeric_targets": unassigned == 0 and all(r["passes_target"] for r in rows),
        "physical_qualification": False,
        "scope": "Labeled detection counts only. Requires complete live observation, independent source/timeline/label review and separate greeting outcomes. No physical origin or desk-observation qualification.",
    }
