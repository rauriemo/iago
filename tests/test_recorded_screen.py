"""Validate a reviewed screen evidence bundle, not automatic physical capture attestation."""

import os
from pathlib import Path

import pytest

from reachy_brain.evals.screen_grounding import (
    ScreenCapture,
    ScreenPlan,
    ScreenReview,
    digest,
    score_screen,
    verify_images,
)
from reachy_brain.evals.visual_capture import CaseMapping, validate_screen_transcript


@pytest.mark.live_pc
@pytest.mark.features("V9")
@pytest.mark.scenario("V9-REVIEWED-RECORDED-SCREEN")
def test_reviewed_screen_evidence_bundle(record_property):
    configured = os.environ.get("IAGO_PC_SCREEN_FIXTURES")
    if not configured:
        pytest.skip("IAGO_PC_SCREEN_FIXTURES required; see docs/iago/SCREEN_EVAL.md")
    root = Path(configured).resolve()
    data = {}
    for name in ("plan", "capture", "review", "mapping", "session"):
        path = (root / (name + ".json")).resolve()
        assert path.is_relative_to(root), "screen fixture outside root"
        if not path.is_file():
            pytest.skip("Required screen evidence unavailable: " + name + ".json")
        limit = (32 if name == "session" else 1) * 1024 * 1024
        with path.open("rb") as stream:
            data[name] = stream.read(limit + 1)
        assert len(data[name]) <= limit, "screen fixture size limit"
    images = (root / "images").resolve()
    assert images.is_relative_to(root), "screen images outside root"
    if not images.is_dir():
        pytest.skip("Required screen images directory unavailable")
    plan = ScreenPlan.model_validate_json(data["plan"])
    capture = ScreenCapture.model_validate_json(data["capture"])
    review = ScreenReview.model_validate_json(data["review"])
    mapping = CaseMapping.model_validate_json(data["mapping"])
    binding = validate_screen_transcript(plan, mapping, data["session"], capture)
    try:
        artifacts = verify_images(images, plan, capture)
    except FileNotFoundError:
        pytest.skip("Required retained screen image artifact unavailable")
    result = score_screen(plan, capture, review)
    record_property("sample_count", result["cases"])
    record_property("fixture_kind", "user-supplied reviewed screen evidence; origin requires audit")
    record_property(
        "measurements",
        {
            "score": result,
            "transcript_binding": binding,
            "image_artifacts": artifacts,
            "record_sha256": [digest(r) for r in (plan, capture, review)],
            "limitation": "Checks recorded evidence consistency and reviewed scoring only. Physical picker execution, frozen-target chronology and actual model request linkage still require independent audit.",
        },
    )
    assert result["passes_scored_checks"], "Recorded screen grounding gate failed"
