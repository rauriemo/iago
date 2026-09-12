"""Synthetic scoring records, never physical whiteboard/phone qualification."""

import pytest

from reachy_brain.evals.visual_grounding import (
    Reference,
    VisualAnswer,
    VisualCapture,
    VisualCase,
    VisualPlan,
    VisualReview,
    VisualVerdict,
    digest,
    score_visual,
)

pytestmark = [
    pytest.mark.features("V2", "V4", "V6", "V8"),
    pytest.mark.scenario("VISUAL-GROUNDING-SCORER"),
]


def records():
    categories = [
        "current",
        "show_then_lower",
        "before_after",
        "history_1",
        "history_5",
        "history_9",
        "crop",
        "blur",
        "glare",
        "small_text",
        "upload",
        "expired",
    ]
    cases, answers, verdicts = [], [], {}
    for i, category in enumerate(categories):
        uncertain = category in {"blur", "glare", "small_text", "expired"}
        refs = (
            []
            if category == "expired"
            else [
                Reference(
                    frame_id=f"frame-{i}",
                    source_id="synthetic",
                    captured_at=float(i),
                    image_sha256="a" * 64,
                )
            ]
        )
        labels = {} if uncertain else {"label": f"READ-{i}"}
        cases.append(
            VisualCase(
                id=str(i),
                medium="phone" if i % 2 else "whiteboard",
                scenarios=[category],
                references=refs,
                labels=labels,
                uncertainty_expected=uncertain,
            )
        )
        answers.append(
            VisualAnswer(
                case_id=str(i),
                text=" ".join(labels.values()) or "I cannot read it.",
                citations=refs,
                readings=labels,
            )
        )
        verdicts[str(i)] = VisualVerdict(
            scenario_execution_verified=True,
            uncertainty_appropriate=True,
            no_invented_reading=True,
            reasoning="useful",
            assessment="Synthetic verdict tests only the scorer.",
        )
    plan = VisualPlan(version=1, frozen_at=10.0, cases=cases)
    capture = VisualCapture(
        plan_sha256=digest(plan), started_at=11.0, model="gpt-6-astra", answers=answers
    )
    review = VisualReview(
        capture_sha256=digest(capture),
        reviewer="synthetic-test",
        independent_human_review=True,
        verdicts=verdicts,
    )
    return plan, capture, review


def test_separate_reading_selection_and_human_judgments():
    plan, capture, review = records()
    capture.answers[0].text = "WRONG"
    capture.answers[0].readings = {"label": "WRONG"}
    capture.answers[1].citations = []
    review.verdicts["2"].reasoning = "unhelpful"
    review.verdicts["7"].uncertainty_appropriate = False
    review.capture_sha256 = digest(capture)
    result = score_visual(plan, capture, review)
    assert result["exact_labels_correct"] == result["exact_labels"] - 1
    assert result["reference_selection_correct"] == 11
    assert result["cases"][2]["reasoning"] == "unhelpful"
    assert result["cases"][7]["uncertainty_appropriate"] is False
    assert result["physical_capture_verified"] is False
    assert result["release_validated"] is False
    assert result["scenario_counts"]["before_after"] == 1
    assert result["medium_counts"] == {"whiteboard": 6, "phone": 6}


@pytest.mark.parametrize("all_uncertain", [False, True])
def test_uncertainty_cannot_remove_positive_reading_coverage(all_uncertain):
    plan, capture, review = records()
    for case in plan.cases if all_uncertain else plan.cases[:1]:
        case.uncertainty_expected = True
        case.labels = {}
        if "blur" not in case.scenarios:
            case.scenarios.append("blur")
    capture.plan_sha256 = digest(plan)
    review.capture_sha256 = digest(capture)
    with pytest.raises(ValueError, match="readable_scenario_coverage_required"):
        score_visual(plan, capture, review)


def test_both_media_require_readable_material():
    plan, capture, review = records()
    for case in plan.cases:
        case.medium = "phone" if case.uncertainty_expected else "whiteboard"
    capture.plan_sha256 = digest(plan)
    review.capture_sha256 = digest(capture)
    with pytest.raises(ValueError, match="readable_both_media_required"):
        score_visual(plan, capture, review)


@pytest.mark.parametrize(
    "change,error",
    [
        ("binding", "evidence_binding_mismatch"),
        ("time", "plan_must_precede_capture"),
        ("duplicate", "duplicate_case_id"),
        ("review", "case_coverage_mismatch"),
        ("self", "independent_human_review_required"),
        ("category", "required_scenarios_missing"),
        ("medium", "both_media_required"),
        ("uncertainty", "uncertainty_case_required"),
        ("expired", "expired_evidence_must_be_unavailable"),
        ("span", "reading_not_verbatim_answer_span"),
    ],
)
def test_rejects_incomplete_or_mismatched_records(change, error):
    plan, capture, review = records()
    if change == "time":
        capture.started_at = plan.frozen_at
    elif change == "duplicate":
        capture.answers.append(capture.answers[0])
    elif change == "review":
        review.verdicts.pop("0")
    elif change == "self":
        review.independent_human_review = False
    elif change == "category":
        plan.cases[0].scenarios = ["crop"]
    elif change == "medium":
        for case in plan.cases:
            case.medium = "phone"
    elif change == "uncertainty":
        plan.cases[7].uncertainty_expected = False
    elif change == "expired":
        plan.cases[-1].references = plan.cases[0].references
    elif change == "span":
        capture.answers[0].text = "Missing expected span"
    capture.plan_sha256 = digest(plan)
    review.capture_sha256 = digest(capture)
    if change == "binding":
        capture.answers[0].text += " changed"
    with pytest.raises(ValueError, match=error):
        score_visual(plan, capture, review)


def test_file_report_requires_exact_images_and_preserves_output(tmp_path, monkeypatch):
    import hashlib
    import json

    from PIL import Image

    from reachy_brain.evals.visual_grounding import main

    image = tmp_path / "image.jpg"
    Image.new("RGB", (8, 8), "white").save(image)
    sha = hashlib.sha256(image.read_bytes()).hexdigest()
    image.rename(tmp_path / f"{sha}.jpg")
    plan, capture, review = records()
    for case in plan.cases:
        for ref in case.references:
            ref.image_sha256 = sha
    for answer, case in zip(capture.answers, plan.cases, strict=True):
        answer.citations = case.references
    capture.plan_sha256 = digest(plan)
    review.capture_sha256 = digest(capture)
    argv = ["visual_grounding", "--image-root", str(tmp_path)]
    for name, record in zip(("plan", "capture", "review"), (plan, capture, review), strict=True):
        path = tmp_path / f"{name}.json"
        path.write_text(record.model_dump_json(), encoding="utf-8")
        argv += [f"--{name}", str(path)]
    output = tmp_path / "result.json"
    argv += ["--output", str(output)]
    monkeypatch.setattr("sys.argv", argv)
    assert main() == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["physical_capture_verified"] is False
    with pytest.raises(FileExistsError):
        main()
    (tmp_path / f"{sha}.jpg").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="hash"):
        main()
