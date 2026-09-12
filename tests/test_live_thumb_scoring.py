"""Synthetic labeled intervals verify arithmetic, not physical camera performance."""

import pytest

from reachy_brain.core.gesture_activity import GestureActivity
from reachy_brain.evals.gesture_observation import GestureSnapshot
from reachy_brain.evals.live_thumbs import LiveThumbPlan, score_live_thumbs
from reachy_brain.evals.thumb_clips import NEGATIVES

pytestmark = [
    pytest.mark.features("P10", "D6"),
    pytest.mark.scenario("LIVE-THUMB-INTERVAL-SCORING"),
]


def dataset(case="valid"):
    now = [0.0]
    activity = GestureActivity(clock=lambda: now[0])
    start = GestureSnapshot.model_validate(activity.snapshot())
    cases = []
    negatives = sorted(NEGATIVES)
    for i in range(60):
        label = "thumb_up" if i < 20 else "thumb_down" if i < 40 else "negative"
        row = dict(
            id=str(i),
            label=label,
            category=label if i < 40 else negatives[i % 8],
            start=float(i * 3),
            end=float(i * 3 + 3),
            source="camera",
            generation=1,
            session="session",
            question="q" + str(i),
            turn=i,
            people=1,
            orientation=str(i % 2),
            distance=str(i % 2),
            lighting=str(i % 2),
        )
        cases.append(row)
        if case in ("threshold", "miss") and i < (2 if case == "threshold" else 3):
            continue
        if i >= 40 and not (case == "false" and i < 42):
            continue
        now[0] = float(i * 3 + 1)
        response = {k: row[k] for k in ("source", "generation", "session", "question", "turn")}
        response.update(
            slot=str(i),
            value="yes" if i < 20 or i >= 40 else "no",
            gesture="thumb_up" if i < 20 or i >= 40 else "thumb_down",
        )
        if i == 0:
            if case in {"source", "question", "session"}:
                response[case] = "wrong"
            if case in {"generation", "turn"}:
                response[case] += 1
            if case == "polarity":
                response["value"] = "no"
        activity.accepted(response, i + 1)
        if case == "duplicate" and i == 0:
            response["slot"] = "other-slot"
            activity.accepted(response, i + 1)
    now[0] = 180.0
    plan = LiveThumbPlan.model_validate(dict(profile="pc", owner=activity.owner, cases=cases))
    return plan, start, GestureSnapshot.model_validate(activity.snapshot())


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "threshold",
        "miss",
        "false",
        "polarity",
        "duplicate",
        "source",
        "question",
        "session",
        "generation",
        "turn",
    ],
)
def test_original_numeric_and_context_gates(case):
    result = score_live_thumbs(*dataset(case))
    assert result["passes_numeric_targets"] == (case in {"valid", "threshold"})
    assert not result["physical_gesture_validated"] and not result["release_validated"]
    if case == "threshold":
        assert result["groups"]["thumb_up"]["correct"] == 18
    if case in {"source", "question", "session", "generation", "turn"}:
        assert result["invalid_context_responses"] == 1


@pytest.mark.parametrize("case", ["owner", "outside", "overlap", "coverage"])
def test_invalid_dataset_or_unassigned_events_reject(case):
    plan, start, end = dataset()
    if case == "owner":
        plan.owner = "other"
    if case == "outside":
        plan.cases[0].start = 2.0
    if case == "overlap":
        plan.cases[0].end = 4.0
    if case == "coverage":
        plan.cases.pop()
    with pytest.raises(ValueError):
        plan = LiveThumbPlan.model_validate(plan.model_dump())
        score_live_thumbs(plan, start, end)
