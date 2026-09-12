"""Reviewed physical recordings required; text comparison alone never qualifies this gate."""

import json
import os
from pathlib import Path

import pytest

from reachy_brain.evals.acoustics import CutoffTrial, measure_cutoff
from reachy_brain.evals.opening_words import (
    OpeningMapping,
    OpeningPlan,
    OpeningReview,
    score_openings,
    validate_opening_review,
)


@pytest.mark.live_pc
@pytest.mark.features("C1", "C2", "D5")
@pytest.mark.scenario("PHYSICAL-PC-OPENING-WORDS")
def test_reviewed_recorded_interruption_openings(record_property):
    score_recorded_openings(record_property, profile="pc")


def validate_profile(manifest, profile):
    expected = (
        "physical-pc-independent-recording"
        if profile == "pc"
        else "physical-robot-independent-recording"
    )
    assert manifest["fixture_kind"] == expected, "recording platform mismatch"
    if profile != "pc":
        assert profile in {"reachy_pc", "reachy_local"}
        assert manifest.get("profile") == profile, "recording deployment profile mismatch"
        for key in ("robot_identity", "daemon_version", "configuration_sha256"):
            assert manifest.get(key), "missing " + key


def score_recorded_openings(record_property, *, profile):
    variable = (
        "IAGO_PC_OPENING_FIXTURES"
        if profile == "pc"
        else "IAGO_ROBOT_" + profile.upper() + "_OPENING_FIXTURES"
    )
    configured = os.environ.get(variable)
    if not configured:
        pytest.skip(variable + " required; see docs/iago/OPENING_WORDS_EVAL.md and ROBOT_EVAL.md")
    root = Path(configured).resolve()
    required = {
        "plan.json": 1024 * 1024,
        "mapping.json": 1024 * 1024,
        "review.json": 1024 * 1024,
        "acoustics.json": 1024 * 1024,
        "session.json": 32 * 1024 * 1024,
    }
    data = {}
    for name, limit in required.items():
        path = (root / name).resolve()
        assert path.is_relative_to(root), "fixture outside root"
        if not path.is_file():
            pytest.skip("Required opening fixture file unavailable: " + name)
        with path.open("rb") as stream:
            data[name] = stream.read(limit + 1)
        assert len(data[name]) <= limit, "fixture size limit"
    plan = OpeningPlan.model_validate_json(data["plan.json"])
    mapping = OpeningMapping.model_validate_json(data["mapping.json"])
    review = OpeningReview.model_validate_json(data["review.json"])
    manifest = json.loads(data["acoustics.json"])
    validate_profile(manifest, profile)
    assert all(
        manifest.get(k)
        for k in ("operator", "recorded_at", "application_revision", "devices", "voice_ids")
    )
    assert 20 <= len(manifest["trials"]) <= 1000
    trials = [CutoffTrial.model_validate(t) for t in manifest["trials"]]
    selected = validate_opening_review(plan, mapping, review, data["acoustics.json"], trials)
    assert len({(t.sha256, t.event_sample) for t in selected}) == len(selected), (
        "duplicate recorded events"
    )
    measurements = [measure_cutoff(root, trial) for trial in selected]
    result = score_openings(plan, mapping, data["session.json"])
    record_property("sample_count", result["total"])
    record_property(
        "measurements",
        {
            "profile": profile,
            "openings": result,
            "recordings": measurements,
            "review_mapping_sha256": review.mapping_sha256,
            "review_acoustic_sha256": review.acoustic_manifest_sha256,
        },
    )
    assert result["all_openings_preserved"], "Opening words missing from recorded recognition"
