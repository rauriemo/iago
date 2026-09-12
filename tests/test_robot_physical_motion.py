"""Reviewed independent pose evidence, never command-log motion qualification."""

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import pytest
from pydantic import Field
from test_physical_opening_words import validate_profile

from reachy_brain.core.ownership import PlaybackGuard
from reachy_brain.evals.robot_motion_bundle import MotionBundle, score_bundle
from reachy_brain.evals.screen_grounding import StrictRecord


class Trial(StrictRecord):
    id: str = Field(min_length=1, max_length=128)
    trigger: Literal["pc_pause", "pc_terminate", "connection_loss"]
    bundle: MotionBundle


@pytest.mark.robot
@pytest.mark.features("D2", "P9", "D5")
@pytest.mark.scenario("ROBOT-PC-LOSS-RECORDED-MOTION")
def test_recorded_pc_loss_motion(record_property):
    configured = os.environ.get("IAGO_ROBOT_MOTION_MANIFEST")
    if not configured or not Path(configured).is_file():
        pytest.skip("IAGO_ROBOT_MOTION_MANIFEST required; see docs/iago/ROBOT_EVAL.md")
    path = Path(configured).resolve()
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    assert len(data) <= 1024 * 1024, "manifest size limit"
    manifest = json.loads(data)
    validate_profile(manifest, "reachy_pc")
    for key in ("operator", "reviewer", "recorded_at", "application_revision", "devices"):
        assert manifest.get(key), "missing " + key
    assert isinstance(manifest["trials"], list) and 3 <= len(manifest["trials"]) <= 1000
    trials = [Trial.model_validate(row) for row in manifest["trials"]]
    assert len({t.id for t in trials}) == len(trials), "duplicate trial IDs"
    assert len({t.bundle.trace.sha256 for t in trials}) == len(trials), "reused motion trace"
    assert {t.trigger for t in trials} == {"pc_pause", "pc_terminate", "connection_loss"}, (
        "missing loss trigger"
    )
    rows = []
    try:
        for trial in trials:
            result = score_bundle(
                path.parent,
                trial.bundle,
                expected_trigger=trial.trigger,
                expected_profile="reachy_pc",
            )
            rows.append({"id": trial.id, "trigger": trial.trigger, **result})
            assert result["lease_seconds"] == PlaybackGuard().lease_seconds, (
                "current runtime lease required"
            )
            assert result["pre_event_motion_observed"], (
                "idle trace cannot establish motion stopping"
            )
            assert not result["motion_observed_after_lease"], "observed motion after lease"
    finally:
        record_property("sample_count", len(rows))
        record_property(
            "measurements",
            {
                "manifest_sha256": hashlib.sha256(data).hexdigest(),
                "trials": rows,
                "scope": "Reviewed recorded pose gate only; sensor origin/calibration and unobserved motion require independent audit. Not complete physical qualification.",
            },
        )
