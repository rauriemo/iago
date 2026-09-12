"""Validate a reviewed visual evidence bundle, not automatic physical capture attestation."""

import os
from pathlib import Path

import pytest

from reachy_brain.evals.visual_capture import CaseMapping, validate_capture_transcript
from reachy_brain.evals.visual_grounding import (
    VisualCapture,
    VisualPlan,
    VisualReview,
    digest,
    score_visual,
    verify_images,
)


@pytest.mark.live_pc
@pytest.mark.features("V2", "V4", "V6", "V8")
@pytest.mark.scenario("VISUAL-REVIEWED-RECORDED-BUNDLE")
def test_reviewed_visual_evidence_bundle(record_property):
    configured = os.environ.get("IAGO_PC_VISUAL_FIXTURES")
    if not configured:
        pytest.skip("IAGO_PC_VISUAL_FIXTURES required; see docs/iago/VISUAL_EVAL.md")
    root = Path(configured).resolve()
    data = {}
    for name in ("plan", "capture", "review", "mapping", "session"):
        path = (root / (name + ".json")).resolve()
        assert path.is_relative_to(root), "visual fixture outside root"
        if not path.is_file():
            pytest.skip("Required visual evidence unavailable: " + name + ".json")
        limit = (32 if name == "session" else 1) * 1024 * 1024
        with path.open("rb") as stream:
            data[name] = stream.read(limit + 1)
        assert len(data[name]) <= limit, "visual fixture size limit"
    images = (root / "images").resolve()
    assert images.is_relative_to(root), "visual images outside root"
    if not images.is_dir():
        pytest.skip("Required visual images directory unavailable")
    plan = VisualPlan.model_validate_json(data["plan"])
    capture = VisualCapture.model_validate_json(data["capture"])
    review = VisualReview.model_validate_json(data["review"])
    mapping = CaseMapping.model_validate_json(data["mapping"])
    binding = validate_capture_transcript(plan, mapping, data["session"], capture)
    try:
        artifacts = verify_images(images, plan, capture)
    except FileNotFoundError:
        pytest.skip("Required retained visual image artifact unavailable")
    result = score_visual(plan, capture, review)
    record_property("sample_count", len(result["cases"]))
    record_property("fixture_kind", "user-supplied reviewed visual evidence; origin requires audit")
    record_property(
        "measurements",
        {
            "score": result,
            "transcript_binding": binding,
            "image_artifacts": artifacts,
            "record_sha256": [digest(r) for r in (plan, capture, review)],
            "limitation": "Checks recorded evidence consistency and reviewed scoring only. Physical origin and frozen-target chronology require independent audit. Reading, reference selection, uncertainty and reasoning are reported separately without inventing an aggregate quality threshold.",
        },
    )
    assert all(case["scenario_execution_verified"] for case in result["cases"]), (
        "Unverified visual scenario execution"
    )
