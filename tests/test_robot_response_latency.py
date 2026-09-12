"""Reviewed robot-speaker response timing; no synthetic or PC qualification."""

import pytest
from test_physical_response_latency import score_recorded_responses


@pytest.mark.robot
@pytest.mark.features("C1", "D2", "D3", "D6")
@pytest.mark.scenario("ROBOT-PHYSICAL-RESPONSE-LATENCY")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
def test_robot_recorded_response_latency(profile, record_property):
    score_recorded_responses(record_property, profile=profile)
