"""Independent physical recordings and human review are mandatory for echo evidence."""

import os
from pathlib import Path

import pytest

from reachy_brain.evals.echo_observation import (
    EchoCapture,
    EchoReview,
    RobotEchoCapture,
    score_echo,
)
from reachy_brain.evals.speech_observation import ActivitySnapshot


@pytest.mark.live_pc
@pytest.mark.features("C2", "C5", "C6", "D6")
@pytest.mark.scenario("PHYSICAL-PC-REVIEWED-ECHO")
def test_reviewed_ten_minute_echo_observation(record_property):
    score_recorded_echo(record_property, profile="pc")


def score_recorded_echo(record_property, *, profile):
    variable = (
        "IAGO_PC_ECHO_FIXTURES"
        if profile == "pc"
        else "IAGO_ROBOT_" + profile.upper() + "_ECHO_FIXTURES"
    )
    configured = os.environ.get(variable)
    if not configured:
        pytest.skip(variable + " required; see docs/iago/SPEECH_OBSERVATION.md and ROBOT_EVAL.md")
    root = Path(configured).resolve()
    values = []
    for name, schema in (
        ("capture.json", EchoCapture if profile == "pc" else RobotEchoCapture),
        ("review.json", EchoReview),
        ("start.json", ActivitySnapshot),
        ("end.json", ActivitySnapshot),
    ):
        path = (root / name).resolve()
        assert path.is_relative_to(root), "echo fixture outside root"
        if not path.is_file():
            pytest.skip("Required echo fixture unavailable: " + name)
        with path.open("rb") as stream:
            raw = stream.read(512 * 1024 + 1)
        assert len(raw) <= 512 * 1024, "echo fixture size limit"
        values.append(schema.model_validate_json(raw))
    if profile != "pc":
        assert values[0].profile == profile, "recording deployment profile mismatch"
    try:
        result = score_echo(root, *values)
    except FileNotFoundError:
        pytest.skip("Required independent echo recording unavailable")
    record_property("sample_count", len(result["activity"]["events"]))
    record_property("measurements", {"profile": profile, **result})
    assert result["passes_reviewed_echo_check"], "Self-triggered or unclassified speech events"
