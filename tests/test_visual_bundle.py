"""Synthetic disk fixtures exercise visual evidence processing, never physical qualification."""

import hashlib
import io

import pytest
from PIL import Image
from test_recorded_visual import test_reviewed_visual_evidence_bundle as run_bundle
from test_visual_capture import bind, fixture
from test_visual_grounding import records

from reachy_brain.evals.visual_capture import extract_answers
from reachy_brain.evals.visual_grounding import VisualCapture, digest


@pytest.mark.features("V2", "V4", "V6", "V8")
@pytest.mark.scenario("VISUAL-BUNDLE-FILE-VALIDATION")
@pytest.mark.parametrize(
    "change", [None, "image", "answer", "missing_image", "unreviewed", "chronology", "measurements"]
)
def test_visual_bundle_file_paths_and_separate_measurements(tmp_path, monkeypatch, change):
    plan, mapping, export = fixture("visual")
    _, _, review = records()
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(buffer, format="PNG")
    image = buffer.getvalue()
    image_hash = hashlib.sha256(image).hexdigest()
    for case in plan.cases:
        for ref in case.references:
            ref.image_sha256 = image_hash
    for row in export["entries"]:
        for ref in row["metadata"]["evidence_refs"]:
            ref["image_sha256"] = image_hash
    mapping.plan_sha256 = digest(plan)
    transcript = bind(mapping, export)
    capture = VisualCapture(
        plan_sha256=digest(plan),
        model="gpt-6-astra",
        started_at=plan.frozen_at + 1,
        answers=extract_answers(plan, mapping, transcript)["answers"],
    )
    for case, answer in zip(plan.cases, capture.answers, strict=True):
        answer.readings = dict(case.labels)
    if change == "answer":
        capture.answers[0].text += " Altered answer."
    elif change == "chronology":
        capture.started_at = plan.frozen_at
    elif change == "unreviewed":
        review.verdicts["0"].scenario_execution_verified = False
    elif change == "measurements":
        capture.answers[0].readings.clear()
        review.verdicts["0"].reasoning = "unhelpful"
        review.verdicts["7"].uncertainty_appropriate = False
    review.capture_sha256 = digest(capture)
    for name, record in (
        ("plan", plan),
        ("mapping", mapping),
        ("capture", capture),
        ("review", review),
    ):
        (tmp_path / (name + ".json")).write_text(record.model_dump_json(), encoding="utf-8")
    (tmp_path / "session.json").write_bytes(transcript)
    (tmp_path / "images").mkdir()
    if change != "missing_image":
        (tmp_path / "images" / (image_hash + ".png")).write_bytes(
            image + (b"changed" if change == "image" else b"")
        )
    monkeypatch.setenv("IAGO_PC_VISUAL_FIXTURES", str(tmp_path))
    observed = {}
    errors = {
        "image": (ValueError, "image_artifact_hash_mismatch"),
        "answer": (ValueError, "capture_answer_differs"),
        "chronology": (ValueError, "plan_must_precede_capture"),
        "missing_image": (pytest.skip.Exception, "image"),
        "unreviewed": (AssertionError, "Unverified visual scenario"),
    }
    if change in errors:
        error, message = errors[change]
        with pytest.raises(error, match=message):
            run_bundle(observed.__setitem__)
    else:
        run_bundle(observed.__setitem__)
        score = observed["measurements"]["score"]
        assert observed["sample_count"] == 12
        assert score["physical_capture_verified"] is False and score["release_validated"] is False
        assert score["medium_counts"] == {"whiteboard": 6, "phone": 6}
        assert score["exact_labels_correct"] == score["exact_labels"] - (change == "measurements")
        if change == "measurements":
            assert score["cases"][0]["reasoning"] == "unhelpful"
            assert score["cases"][7]["uncertainty_appropriate"] is False
