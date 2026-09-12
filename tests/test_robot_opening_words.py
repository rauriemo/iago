"""Physical robot recordings and frozen independent review, not synthetic speech."""

import pytest
from test_physical_opening_words import score_recorded_openings


@pytest.mark.robot
@pytest.mark.features("C1", "C2", "D2", "D3", "D5")
@pytest.mark.scenario("ROBOT-PHYSICAL-OPENING-WORDS")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
def test_robot_reviewed_opening_words(profile, record_property):
    score_recorded_openings(record_property, profile=profile)
