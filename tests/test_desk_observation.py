"""Synthetic natural-observation records do not qualify a real desk session."""

import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.evals.behavior_observation import BehaviorSnapshot
from reachy_brain.evals.desk_observation import DeskReview, score_desk


@pytest.mark.features("P1", "P3", "P4", "P8", "D6")
@pytest.mark.scenario("DESK-OBSERVATION-EVENT-ACCOUNTING")
@pytest.mark.parametrize(
    "case",
    ["valid", "short", "missing_review", "uncertain", "duplicate", "outside", "changed_owner"],
)
def test_complete_event_review_and_duration(case):
    engine = BehaviorEngine()
    snapshots = [BehaviorSnapshot.model_validate(engine.observations())]
    labels = []
    for i, kind in enumerate(("person_entered_view", "wave_detected")):
        event = Event(str(i), "camera", kind, float(100 + i), 0.99)
        engine.offer(event, now=event.captured)
        engine.offer(event, now=event.captured + 1)
        snapshots.append(BehaviorSnapshot.model_validate(engine.observations()))
        labels.append(
            dict(
                event=str(i),
                source="camera",
                generation=0,
                classification="correct" if i == 0 else "false",
                greeting_observed=i == 1,
            )
        )
    review = DeskReview.model_validate(
        dict(
            started=0.0,
            ended=1800.0,
            natural_workload_reviewed=True,
            detector_continuity_reviewed=True,
            timeline_alignment_reviewed=True,
            events=labels,
        )
    )
    if case == "short":
        review.ended = 1799.0
    if case == "missing_review":
        review.events.pop()
    if case == "uncertain":
        review.events[0].classification = "uncertain"
    if case == "duplicate":
        review.events.append(review.events[0])
    if case == "outside":
        review.started, review.ended = 110.0, 1910.0
    if case == "changed_owner":
        snapshots[-1].owner = "other"
    if case in {"valid", "uncertain"}:
        result = score_desk(snapshots, review)
        assert result["event_count"] == 2 and result["decision_record_count"] == 4
        assert result["groups"]["wave_detected"]["false"] == 1
        assert result["false_event_greetings"] == 1
        assert result["review_complete"] == (case == "valid")
        assert not result["physical_qualification"]
        assert "passes_target" not in result
    else:
        with pytest.raises(ValueError):
            score_desk(snapshots, review)
