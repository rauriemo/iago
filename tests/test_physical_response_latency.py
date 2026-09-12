"""Real recordings required; deterministic PCM never qualifies physical response latency."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from test_physical_opening_words import validate_profile

from reachy_brain.evals.response_acoustics import (
    ResponseTrial,
    measure_response,
    summarize_responses,
)


@pytest.mark.live_pc
@pytest.mark.features("C1", "D6")
@pytest.mark.scenario("PHYSICAL-PC-RESPONSE-LATENCY")
def test_independently_recorded_response_latency(record_property):
    score_recorded_responses(record_property, profile="pc")


def score_recorded_responses(record_property, *, profile):
    variable = (
        "IAGO_PC_RESPONSE_MANIFEST"
        if profile == "pc"
        else "IAGO_ROBOT_" + profile.upper() + "_RESPONSE_MANIFEST"
    )
    configured = os.environ.get(variable)
    if not configured:
        pytest.skip(variable + " required; see docs/iago/RESPONSE_EVAL.md and ROBOT_EVAL.md")
    path = Path(configured).resolve()
    if not path.is_file():
        pytest.skip("Configured physical response manifest is unavailable")
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    assert len(data) <= 1024 * 1024, "manifest size limit"
    manifest = json.loads(data)
    validate_profile(manifest, profile)
    metadata = {
        k: manifest[k]
        for k in (
            "operator",
            "recorded_at",
            "application_revision",
            "devices",
            "voice_ids",
        )
    }
    assert all(metadata.values())
    if profile != "pc":
        metadata.update(
            {
                key: manifest[key]
                for key in ("robot_identity", "daemon_version", "configuration_sha256")
            }
        )
    trials = [ResponseTrial.model_validate(t) for t in manifest["trials"]]
    assert 1 <= len(trials) <= 1000
    assert len({t.id for t in trials}) == len(trials), "duplicate trial IDs"
    assert len({(t.sha256, t.speech_end_sample) for t in trials}) == len(trials), "duplicate events"
    measurements = []
    try:
        for trial in trials:
            measurements.append(measure_response(path.parent, trial))
    finally:
        record_property("sample_count", len(measurements))
        record_property(
            "measurements",
            dict(
                manifest_sha256=hashlib.sha256(data).hexdigest(),
                profile=profile,
                trials=measurements,
                groups=summarize_responses(measurements),
                declared_recording_metadata=metadata,
            ),
        )
    groups = summarize_responses(measurements)
    record_property(
        "expected",
        "Ordinary response median <3000 ms and p95 <5000 ms per voice; retrieval separate",
    )
    for provider in ("openai", "elevenlabs", "fallback"):
        key = f"{provider}/ordinary"
        assert key in groups, f"missing {key} measurements"
        assert groups[key]["passes_target"], f"ordinary latency target failed: {key}"
