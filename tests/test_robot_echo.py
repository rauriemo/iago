"""Robot echo qualification requires independent recordings and reviewed continuity."""

import pytest
from test_physical_echo import score_recorded_echo


@pytest.mark.robot
@pytest.mark.features("C2", "C5", "C6", "D2", "D3", "D6")
@pytest.mark.scenario("ROBOT-PHYSICAL-REVIEWED-ECHO")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
def test_robot_reviewed_echo(profile, record_property):
    score_recorded_echo(record_property, profile=profile)
