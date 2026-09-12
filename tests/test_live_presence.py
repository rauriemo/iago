"""Synthetic entry counts cannot qualify a physical presence run."""

import pytest

from reachy_brain.evals.live_presence import EntryDetection, EntryPlan, score_entries


@pytest.mark.features("P1", "P4", "P6", "D6")
@pytest.mark.scenario("LIVE-PRESENCE-ENTRY-SCORING")
@pytest.mark.parametrize(
    "case",
    ["valid", "missed", "repeated", "startup", "occlusion", "source", "generation", "outside"],
)
def test_ten_entries_and_negative_intervals(case):
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
    events = [
        EntryDetection(
            id=str(i), source="camera", generation=1, at=float(i * 10 + 1), greeted=False
        )
        for i in range(10)
    ]
    if case == "missed":
        events.pop()
    if case in {"repeated", "startup", "occlusion", "outside"}:
        events.append(
            EntryDetection(
                id="extra",
                greeted=case == "startup",
                source="camera",
                generation=1,
                at={"repeated": 2.0, "startup": 101.0, "occlusion": 111.0, "outside": 121.0}[case],
            )
        )
    if case == "source":
        events[0].source = "other"
    if case == "generation":
        events[0].generation = 2
    result = score_entries(plan, events)
    assert result["passes_numeric_targets"] == (case == "valid")
    assert not result["physical_qualification"]
