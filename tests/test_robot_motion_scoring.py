"""Synthetic pose traces validate arithmetic, never physical motion hold."""

import pytest
from pydantic import ValidationError

from reachy_brain.evals.robot_motion import MotionTrace, score_motion


def trace():
    return {
        "event_at": 0.5,
        "clock_uncertainty": 0,
        "lease_seconds": 1,
        "measurement_error": [0.001] * 8,
        "samples": [{"at": i / 20, "pose": [min(i / 20, 0.6)] + [0] * 7} for i in range(51)],
    }


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-TRACE-SCORING")
@pytest.mark.parametrize("case", ["hold", "late", "drift", "idle"])
def test_pose_excursion_and_pre_event_motion(case):
    data = trace()
    if case == "late":
        data["samples"][40]["pose"][7] = 0.1
    if case == "drift":
        for i, row in enumerate(data["samples"][30:]):
            row["pose"][0] += 0.001 * i
    if case == "idle":
        for row in data["samples"]:
            row["pose"] = [0] * 8
    result = score_motion(MotionTrace.model_validate(data))
    assert result["pre_event_motion_observed"] == (case != "idle")
    assert result["motion_observed_after_lease"] == (case in ("late", "drift"))
    assert not result["physical_origin_verified"] and not result["release_validated"]


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-TRACE-INVALID")
@pytest.mark.parametrize("case", ["gap", "short", "order", "nan", "negative_error"])
def test_incomplete_or_invalid_trace_rejected(case):
    data = trace()
    if case == "gap":
        del data["samples"][10:15]
    if case == "short":
        data["samples"] = data["samples"][:32]
    if case == "order":
        data["samples"][1]["at"] = 0
    if case == "nan":
        data["samples"][1]["pose"][0] = float("nan")
    if case == "negative_error":
        data["measurement_error"][0] = -0.1
    with pytest.raises((ValueError, ValidationError)):
        score_motion(MotionTrace.model_validate(data))


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-PAIRWISE-EXCURSION")
@pytest.mark.parametrize("channel", [0, 5, 7])
def test_opposite_excursions_cannot_hide_behind_middle_reference(channel):
    data = trace()
    baseline = data["samples"][30]["pose"][channel]
    data["samples"][31]["pose"][channel] = baseline + 0.0015
    data["samples"][32]["pose"][channel] = baseline - 0.0015
    result = score_motion(MotionTrace.model_validate(data))
    name = ("x", "y", "z", "roll", "pitch", "yaw", "antenna_left", "antenna_right")[channel]
    assert result["channels"][name]["maximum_post_deadline_displacement"] == pytest.approx(0.003)
    assert result["motion_observed_after_lease"]


@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-MOTION-ANGULAR-WRAP")
@pytest.mark.parametrize(
    "values,expected", [([3.141, -3.141], 0.001185307179586), ([-3, 0, 3], 3), ([0, 1, 2], 2)]
)
def test_angular_excursion_uses_shortest_pair_distance(values, expected):
    data = trace()
    for i, row in enumerate(data["samples"][30:]):
        row["pose"][5] = values[i % len(values)]
    result = score_motion(MotionTrace.model_validate(data))
    assert result["channels"]["yaw"]["maximum_post_deadline_displacement"] == pytest.approx(
        expected
    )
