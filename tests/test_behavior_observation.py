"""Synthetic behavior logs test evidence continuity, not detector accuracy."""

import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.evals.behavior_observation import BehaviorSnapshot, compare_decisions


@pytest.mark.features("P1", "P4", "P6", "D6")
@pytest.mark.scenario("BEHAVIOR-OBSERVATION-CONTINUITY")
@pytest.mark.parametrize("case", ["valid", "owner", "gap", "sequence", "identity", "overlap"])
def test_complete_decision_interval_and_invalid_records(case):
    engine = BehaviorEngine()
    start = BehaviorSnapshot.model_validate(engine.observations())
    event = Event("one", "camera", "person_entered_view", 100, 0.99)
    engine.offer(event, now=101)
    if case == "gap":
        for i in range(201):
            engine.offer(Event(str(i), "camera", "person_entered_view", 100, 0.99), now=101)
    if case == "overlap":
        start = BehaviorSnapshot.model_validate(engine.observations())
    engine.offer(event, now=102)
    end = BehaviorSnapshot.model_validate(engine.observations())
    if case == "owner":
        end.owner = "other"
    if case == "sequence":
        end.samples[-1].sequence += 1
    if case in {"identity", "overlap"}:
        end.samples[-1 if case == "identity" else 0].captured = 99.0
    if case == "valid":
        result = compare_decisions(start, end)
        assert len(result["records"]) == 2 and result["record_coverage_complete"]
        assert result["records"][0]["captured"] == 100
        assert not result["physical_qualification"]
    else:
        with pytest.raises(ValueError):
            compare_decisions(start, end)
