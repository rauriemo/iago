"""Real engine decisions with synthetic entries and explicit synthetic greeting review."""

import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.evals.behavior_observation import BehaviorSnapshot
from reachy_brain.evals.live_presence import EntryPlan, GreetingReview, score_observed_entries


@pytest.mark.features("P1", "P4", "P6", "D6")
@pytest.mark.scenario("PRESENCE-OBSERVATION-REVIEW-COVERAGE")
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "missing_review",
        "duplicate_review",
        "wrong_source",
        "late_start",
        "startup_greeting",
    ],
)
def test_lifecycle_records_do_not_inflate_entry_counts(case):
    engine = BehaviorEngine()
    engine.mode = "aware"
    engine.rules["entry"].enabled = True
    engine.rules["entry"].cooldown = 0
    start = BehaviorSnapshot.model_validate(engine.observations())
    plan = EntryPlan.model_validate(
        {
            "cases": [
                dict(
                    id=str(i),
                    label="entry" if i < 10 else "startup" if i == 10 else "occlusion",
                    start=float(i * 10),
                    end=float(i * 10 + 10),
                    source="camera",
                    generation=1,
                )
                for i in range(12)
            ]
        }
    )
    reviews = []
    for i in range(11):
        now = float(i * 10 + 1)
        event = Event(
            str(i),
            "camera",
            "person_entered_view",
            now,
            0.99,
            duration=1,
            generation=1,
            details={"startup": i == 10},
        )
        engine.offer(event, now=now)
        if case == "late_start" and i == 0:
            start = BehaviorSnapshot.model_validate(engine.observations())
        engine.take(now=now)
        engine.offer(event, now=now + 1)
        reviews.append(
            GreetingReview(
                id=str(i),
                source="camera",
                generation=1,
                greeted=i == 10 and case == "startup_greeting",
            )
        )
    end = BehaviorSnapshot.model_validate(engine.observations())
    if case == "missing_review":
        reviews.pop()
    if case == "duplicate_review":
        reviews.append(reviews[0])
    if case == "wrong_source":
        reviews[0].source = "other"
    if case in {"valid", "startup_greeting"}:
        result = score_observed_entries(plan, start, end, reviews)
        assert result["passes_numeric_targets"] == (case == "valid")
        assert [r["count"] for r in result["cases"]] == [1] * 11 + [0]
        assert not result["physical_qualification"]
    else:
        with pytest.raises(ValueError):
            score_observed_entries(plan, start, end, reviews)
