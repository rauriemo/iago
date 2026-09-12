"""Synthetic records test V9 scoring only, never a physical sharing session."""

import pytest

from reachy_brain.evals.screen_grounding import (
    Reference,
    ScreenAnswer,
    ScreenCapture,
    ScreenCase,
    ScreenPlan,
    ScreenReview,
    digest,
    score_screen,
)

pytestmark = [pytest.mark.features("V9"), pytest.mark.scenario("V9-GROUNDING-SCORER")]


def records():
    categories = ["current"] * 3 + ["history_1", "history_5", "history_9"] + ["crop"] * 2
    categories += ["camera_confusion"] * 2 + ["pin_expiry", "source_change", "blank", "unreadable"]
    cases, answers = [], []
    for i, category in enumerate(categories):
        labels = {f"label-{i}-{j}": f"Synthetic value {i}/{j}" for j in range(2)} if i < 10 else {}
        ref = Reference(
            frame_id=f"frame-{i}", source_id="screen", captured_at=float(i), image_sha256="a" * 64
        )
        cases.append(
            ScreenCase(
                id=f"case-{i}", category=category, picker="window", references=[ref], labels=labels
            )
        )
        answers.append(
            ScreenAnswer(
                case_id=f"case-{i}",
                text="; ".join(labels.values()),
                citations=[ref],
                readings=dict(labels),
            )
        )
    plan = ScreenPlan(
        version=1,
        cases=cases,
        unavailable_picker_choices={
            "monitor": "synthetic scorer fixture",
            "tab": "synthetic scorer fixture",
        },
    )
    capture = ScreenCapture(plan_sha256=digest(plan), model="gpt-6-astra", answers=answers)
    review = ScreenReview(
        capture_sha256=digest(capture),
        reviewer="synthetic test reviewer",
        independent_human_review=True,
        no_invented_text={c.id: True for c in cases},
        scenario_execution_verified={c.id: True for c in cases},
    )
    return plan, capture, review


def score_rebound(plan, capture, review):
    capture.plan_sha256 = digest(plan)
    review.capture_sha256 = digest(capture)
    return score_screen(plan, capture, review)


def test_screen_scorer_requires_eighteen_of_twenty_and_never_claims_physical():
    plan, capture, review = records()
    for missing in range(4):
        result = score_rebound(plan, capture, review)
        assert result["exact_labels_correct"] == 20 - missing
        assert result["passes_scored_checks"] == (missing <= 2)
        assert result["physical_capture_verified"] is False
        capture.answers[missing].readings.pop(f"label-{missing}-0")


@pytest.mark.parametrize(
    "change",
    [
        "missing_citation",
        "wrong_source",
        "wrong_time",
        "wrong_hash",
        "extra_citation",
        "invented_blank",
        "unverified_execution",
    ],
)
def test_screen_scorer_cannot_average_away_provenance_or_negative_failures(change):
    plan, capture, review = records()
    answer = capture.answers[0]
    if change == "missing_citation":
        answer.citations.clear()
    elif change.startswith("wrong_"):
        answer.citations = [answer.citations[0].model_copy(deep=True)]
        if change == "wrong_source":
            answer.citations[0].source_id = "camera"
        elif change == "wrong_time":
            answer.citations[0].captured_at += 1
        else:
            answer.citations[0].image_sha256 = "b" * 64
    elif change == "extra_citation":
        answer.citations.append(capture.answers[1].citations[0])
    elif change == "invented_blank":
        review.no_invented_text["case-12"] = False
    else:
        review.scenario_execution_verified["case-0"] = False
    result = score_rebound(plan, capture, review)
    assert result["exact_labels_pass"]
    assert not result["passes_scored_checks"]


@pytest.mark.parametrize(
    "change,error",
    [
        ("duplicate", "duplicate_case_id"),
        ("omission", "case_coverage_mismatch"),
        ("stale_review", "evidence_binding_mismatch"),
        ("self_review", "independent_human_review_required"),
        ("review_omission", "review_coverage_mismatch"),
        ("missing_category", "required_scenarios_missing"),
        ("picker", "picker_coverage_invalid"),
        ("labels", "twenty_unique_labels_required"),
        ("fabricated_span", "reading_not_verbatim_answer_span"),
    ],
)
def test_screen_scorer_rejects_incomplete_or_mismatched_evidence(change, error):
    plan, capture, review = records()
    if change == "duplicate":
        capture.answers.append(capture.answers[0])
    elif change == "omission":
        capture.answers.pop()
    elif change == "self_review":
        review.independent_human_review = False
    elif change == "review_omission":
        review.no_invented_text.pop("case-0")
    elif change == "missing_category":
        plan.cases[3].category = "current"
    elif change == "picker":
        plan.unavailable_picker_choices.pop("monitor")
    elif change == "labels":
        plan.cases[0].labels.clear()
    elif change == "fabricated_span":
        capture.answers[0].text = "No answer text containing the expected values."
    if change != "stale_review":
        capture.plan_sha256 = digest(plan)
        review.capture_sha256 = digest(capture)
    else:
        capture.answers[0].text += " changed after review"
    with pytest.raises(ValueError, match=error):
        score_screen(plan, capture, review)


def test_screen_scorer_file_entrypoint_preserves_evidence(tmp_path, monkeypatch):
    import json

    from reachy_brain.evals.screen_grounding import main

    args = ["screen_grounding"]
    for name, record in zip(("plan", "capture", "review"), records(), strict=True):
        path = tmp_path / f"{name}.json"
        path.write_text(record.model_dump_json(), encoding="utf-8")
        args.extend([f"--{name}", str(path)])
    output = tmp_path / "score.json"
    args.extend(["--output", str(output)])
    monkeypatch.setattr("sys.argv", args)
    assert main() == 0
    original = output.read_bytes()
    result = json.loads(original)
    assert result["physical_capture_verified"] is False
    assert result["image_artifacts_verified"] is False
    assert len(result["record_sha256"]) == 3
    assert b"Synthetic value" not in original
    with pytest.raises(FileExistsError):
        main()
    assert output.read_bytes() == original
    (tmp_path / "capture.json").write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="screen_record_size_limit"):
        main()


@pytest.mark.parametrize(
    "change", ["valid", "tampered", "missing", "not_jpeg", "oversized", "bad_hash"]
)
def test_screen_artifact_verification_uses_actual_bounded_bytes(tmp_path, monkeypatch, change):
    import hashlib
    import io

    from PIL import Image

    from reachy_brain.evals.screen_grounding import main, verify_images

    plan, capture, review = records()
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(buffer, format="PNG" if change == "not_jpeg" else "JPEG")
    data = buffer.getvalue()
    expected = hashlib.sha256(data).hexdigest()
    for case in plan.cases:
        for ref in case.references:
            ref.image_sha256 = expected
    for answer in capture.answers:
        for ref in answer.citations:
            ref.image_sha256 = expected
    if change == "bad_hash":
        plan.cases[0].references[0].image_sha256 = "../private"
    capture.plan_sha256 = digest(plan)
    path = tmp_path / f"{expected}.jpg"
    if change != "missing":
        path.write_bytes(data + b"tampered" if change == "tampered" else data)
    if change == "oversized":
        with path.open("wb") as output:
            output.truncate(64 * 1024 * 1024 + 1)
    if change == "valid":
        assert verify_images(tmp_path, plan, capture) == {"unique_images": 1, "bytes": len(data)}
        import json

        review.capture_sha256 = digest(capture)
        argv = ["screen_grounding", "--image-root", str(tmp_path)]
        for name, record in zip(
            ("plan", "capture", "review"), (plan, capture, review), strict=True
        ):
            record_path = tmp_path / f"{name}.json"
            record_path.write_text(record.model_dump_json(), encoding="utf-8")
            argv.extend([f"--{name}", str(record_path)])
        output_path = tmp_path / "score.json"
        argv.extend(["--output", str(output_path)])
        monkeypatch.setattr("sys.argv", argv)
        assert main() == 0
        result = json.loads(output_path.read_text(encoding="utf-8"))
        assert result["image_artifacts_verified"] is True
        assert result["physical_capture_verified"] is False
    elif change == "missing":
        with pytest.raises(FileNotFoundError):
            verify_images(tmp_path, plan, capture)
    else:
        errors = {
            "tampered": "image_artifact_hash_mismatch",
            "not_jpeg": "invalid_retained_image_format",
            "oversized": "image_artifact_byte_limit",
            "bad_hash": "invalid_image_hash",
        }
        with pytest.raises(ValueError, match=errors[change]):
            verify_images(tmp_path, plan, capture)
