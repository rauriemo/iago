"""Private independent recordings qualify PC-loss audio, not an offline fake edge."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from test_physical_opening_words import validate_profile

from reachy_brain.core.ownership import PlaybackGuard
from reachy_brain.evals.robot_loss import LossTrial, measure_loss, summarize_losses


@pytest.mark.robot
@pytest.mark.features("C2", "D2", "D5")
@pytest.mark.scenario("ROBOT-PC-LOSS-RECORDED-AUDIO")
def test_recorded_pc_loss_stops_robot_audio(record_property):
    configured = os.environ.get("IAGO_ROBOT_PC_LOSS_MANIFEST")
    if not configured or not Path(configured).is_file():
        pytest.skip("IAGO_ROBOT_PC_LOSS_MANIFEST required; see docs/iago/ROBOT_EVAL.md")
    path = Path(configured).resolve()
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    assert len(data) <= 1024 * 1024, "manifest size limit"
    manifest = json.loads(data)
    validate_profile(manifest, "reachy_pc")
    for key in (
        "operator",
        "reviewer",
        "recorded_at",
        "application_revision",
        "devices",
        "voice_ids",
    ):
        assert manifest.get(key), "missing " + key
    assert isinstance(manifest["trials"], list) and 1 <= len(manifest["trials"]) <= 1000
    trials = [LossTrial.model_validate(row) for row in manifest["trials"]]
    assert all(t.lease_seconds == PlaybackGuard().lease_seconds for t in trials), (
        "declared lease differs from the current runtime lease"
    )
    assert len({t.recording.id for t in trials}) == len(trials), "duplicate trial IDs"
    assert len({(t.recording.sha256, t.recording.event_sample) for t in trials}) == len(trials), (
        "duplicate events"
    )
    rows = []
    try:
        for trial in trials:
            rows.append(measure_loss(path.parent, trial))
    finally:
        record_property("sample_count", len(rows))
        record_property(
            "measurements",
            {
                "manifest_sha256": hashlib.sha256(data).hexdigest(),
                "trials": rows,
                "groups": summarize_losses(rows),
            },
        )
    assert {(t.trigger, t.recording.provider) for t in trials} == {
        (trigger, provider)
        for trigger in ("pc_pause", "pc_terminate")
        for provider in ("openai", "elevenlabs", "fallback")
    }, "PC loss trigger/voice coverage missing"
    assert all(row["passes_lease_bound"] for row in rows), (
        "robot audio exceeded declared lease bound"
    )
