"""Synthetic on-disk bundles test the entrypoint; they never qualify physical sharing."""

import hashlib
import io

import pytest
from PIL import Image
from test_recorded_screen import test_reviewed_screen_evidence_bundle as run_bundle
from test_visual_capture import bind, fixture

from reachy_brain.evals.screen_grounding import ScreenCapture, ScreenReview, digest
from reachy_brain.evals.visual_capture import extract_answers


@pytest.mark.features("V9")
@pytest.mark.scenario("SCREEN-BUNDLE-FILE-VALIDATION")
@pytest.mark.parametrize("change", [None, "image", "answer", "score", "missing_image"])
def test_synthetic_bundle_exercises_entrypoint_failure_paths(tmp_path, monkeypatch, change):
    plan, mapping, export = fixture("screen")
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "orange").save(image, format="PNG")
    image_bytes = image.getvalue()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    for case in plan.cases:
        for ref in case.references:
            ref.image_sha256 = image_hash
    for row in export["entries"]:
        for ref in row["metadata"]["evidence_refs"]:
            ref["image_sha256"] = image_hash
    mapping.plan_sha256 = digest(plan)
    transcript = bind(mapping, export)
    capture = ScreenCapture(
        plan_sha256=digest(plan),
        model="gpt-6-astra",
        answers=extract_answers(plan, mapping, transcript)["answers"],
    )
    for case, answer in zip(plan.cases, capture.answers, strict=True):
        answer.readings = dict(case.labels)
    if change == "answer":
        capture.answers[0].text += " Edited after capture."
    elif change == "score":
        for i in range(3):
            capture.answers[i].readings.pop(f"label-{i}-0")
    review = ScreenReview(
        capture_sha256=digest(capture),
        reviewer="synthetic test only",
        independent_human_review=True,
        no_invented_text={c.id: True for c in plan.cases},
        scenario_execution_verified={c.id: True for c in plan.cases},
    )
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
            image_bytes + (b"altered" if change == "image" else b"")
        )
    monkeypatch.setenv("IAGO_PC_SCREEN_FIXTURES", str(tmp_path))
    observations = {}
    if change == "image":
        with pytest.raises(ValueError, match="image_artifact_hash_mismatch"):
            run_bundle(observations.__setitem__)
    elif change == "answer":
        with pytest.raises(ValueError, match="capture_answer_differs"):
            run_bundle(observations.__setitem__)
    elif change == "missing_image":
        with pytest.raises(pytest.skip.Exception, match="image"):
            run_bundle(observations.__setitem__)
    elif change == "score":
        with pytest.raises(AssertionError, match="Recorded screen grounding gate failed"):
            run_bundle(observations.__setitem__)
        assert observations["measurements"]["score"]["exact_labels_correct"] == 17
    else:
        run_bundle(observations.__setitem__)
        assert observations["sample_count"] == 14
        assert observations["measurements"]["score"]["exact_labels_correct"] == 20
        assert observations["measurements"]["score"]["physical_capture_verified"] is False
        assert observations["measurements"]["image_artifacts"]["unique_images"] == 1
