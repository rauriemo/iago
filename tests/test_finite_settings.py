"""Configuration cannot disable retention or cost comparisons with nonfinite numbers."""

import pytest
from pydantic import ValidationError

from reachy_brain.config import Settings


@pytest.mark.features("V3", "V4", "D5", "D6")
@pytest.mark.scenario("FINITE-RESOURCE-SETTINGS")
@pytest.mark.parametrize(
    "field",
    [
        "history_retention_seconds",
        "iago_development_budget",
        "retrieval_deadline_seconds",
        "robot_output_latency_allowance",
        "robot_camera_timing_uncertainty",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_fail_at_configuration_load(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.features("V3", "D5", "D6")
@pytest.mark.scenario("FINITE-SETTINGS-ENV-AND-UNLIMITED")
def test_environment_rejection_and_explicit_unlimited(monkeypatch):
    monkeypatch.setenv("HISTORY_RETENTION_SECONDS", "inf")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("HISTORY_RETENTION_SECONDS", "600")
    monkeypatch.setenv("IAGO_DEVELOPMENT_BUDGET", "nan")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("IAGO_DEVELOPMENT_BUDGET", "unlimited")
    settings = Settings(_env_file=None)
    assert settings.iago_development_budget == "unlimited"
    assert settings.history_retention_seconds == 600
    assert Settings(_env_file=None, iago_development_budget=0).iago_development_budget == 0
