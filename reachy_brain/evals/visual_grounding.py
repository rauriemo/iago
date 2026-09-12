"""Score reviewed whiteboard/phone records; does not capture or qualify physical devices."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from reachy_brain.evals.screen_grounding import Reference, StrictRecord, digest, verify_images


class VisualCase(StrictRecord):
    id: str = Field(min_length=1, max_length=128)
    medium: Literal["whiteboard", "phone"]
    scenarios: list[
        Literal[
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
    ] = Field(min_length=1, max_length=12)
    references: list[Reference] = Field(max_length=8)
    labels: dict[str, str] = Field(max_length=100)
    uncertainty_expected: bool


class VisualPlan(StrictRecord):
    version: Literal[1]
    frozen_at: float = Field(ge=0)
    cases: list[VisualCase] = Field(min_length=10, max_length=200)


class VisualAnswer(StrictRecord):
    case_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=12000)
    citations: list[Reference] = Field(max_length=32)
    readings: dict[str, str] = Field(max_length=100)


class VisualCapture(StrictRecord):
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: float = Field(ge=0)
    model: Literal["gpt-6-astra"]
    answers: list[VisualAnswer] = Field(min_length=10, max_length=200)


class VisualVerdict(StrictRecord):
    scenario_execution_verified: bool
    uncertainty_appropriate: bool
    no_invented_reading: bool
    reasoning: Literal["useful", "mixed", "unhelpful"]
    assessment: str = Field(min_length=1, max_length=2000)


class VisualReview(StrictRecord):
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    independent_human_review: bool
    verdicts: dict[str, VisualVerdict] = Field(max_length=200)


def score_visual(plan: VisualPlan, capture: VisualCapture, review: VisualReview):
    cases = {case.id: case for case in plan.cases}
    answers = {answer.case_id: answer for answer in capture.answers}
    if len(cases) != len(plan.cases) or len(answers) != len(capture.answers):
        raise ValueError("duplicate_case_id")
    if set(cases) != set(answers) or set(cases) != set(review.verdicts):
        raise ValueError("case_coverage_mismatch")
    if capture.plan_sha256 != digest(plan) or review.capture_sha256 != digest(capture):
        raise ValueError("evidence_binding_mismatch")
    if capture.started_at <= plan.frozen_at:
        raise ValueError("plan_must_precede_capture")
    if not review.independent_human_review or not review.reviewer.strip():
        raise ValueError("independent_human_review_required")
    required = {
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
    }
    if {s for c in plan.cases for s in c.scenarios} != required:
        raise ValueError("required_scenarios_missing")
    if {c.medium for c in plan.cases} != {"whiteboard", "phone"}:
        raise ValueError("both_media_required")
    # A blur/expiry tag must not exempt every positive scenario from reading.
    # Cover each positive behavior with at least one independently readable case.
    positive = required - {"blur", "glare", "small_text", "expired"}
    readable = [c for c in plan.cases if not c.uncertainty_expected and c.labels]
    if not positive <= {s for c in readable for s in c.scenarios}:
        raise ValueError("readable_scenario_coverage_required")
    if {c.medium for c in readable} != {"whiteboard", "phone"}:
        raise ValueError("readable_both_media_required")
    results = []
    for case in plan.cases:
        answer, verdict = answers[case.id], review.verdicts[case.id]
        if not verdict.assessment.strip():
            raise ValueError("human_assessment_required")
        if len(set(case.scenarios)) != len(case.scenarios):
            raise ValueError("duplicate_scenario")
        uncertain = bool(set(case.scenarios) & {"blur", "glare", "small_text", "expired"})
        if uncertain and not case.uncertainty_expected:
            raise ValueError("uncertainty_case_required")
        if "expired" in case.scenarios:
            if case.references or case.labels:
                raise ValueError("expired_evidence_must_be_unavailable")
        elif not case.references:
            raise ValueError("expected_reference_required")
        if not uncertain and not case.labels:
            raise ValueError("readable_labels_required")
        if any(
            not k.strip() or len(k) > 128 or not v.strip() or len(v) > 1000
            for k, v in case.labels.items()
        ):
            raise ValueError("invalid_exact_label")
        if not set(answer.readings) <= set(case.labels):
            raise ValueError("unknown_reading_label")
        if any(not v or v not in answer.text for v in answer.readings.values()):
            raise ValueError("reading_not_verbatim_answer_span")
        expected = {digest(ref) for ref in case.references}
        observed = {digest(ref) for ref in answer.citations}
        if len(expected) != len(case.references) or len(observed) != len(answer.citations):
            raise ValueError("duplicate_reference")
        results.append(
            {
                "case_id": case.id,
                "reference_selection_correct": expected == observed,
                "exact_labels": len(case.labels),
                "exact_labels_correct": sum(
                    answer.readings.get(k) == v for k, v in case.labels.items()
                ),
                "uncertainty_expected": case.uncertainty_expected,
                "uncertainty_appropriate": verdict.uncertainty_appropriate,
                "no_invented_reading": verdict.no_invented_reading,
                "reasoning": verdict.reasoning,
                "scenario_execution_verified": verdict.scenario_execution_verified,
            }
        )
    labels = sum(r["exact_labels"] for r in results)
    correct = sum(r["exact_labels_correct"] for r in results)
    return {
        "cases": results,
        "exact_labels": labels,
        "exact_labels_correct": correct,
        "exact_reading_fraction": correct / labels if labels else None,
        "reference_selection_correct": sum(r["reference_selection_correct"] for r in results),
        "scenario_counts": {s: sum(s in c.scenarios for c in plan.cases) for s in sorted(required)},
        "medium_counts": {
            m: sum(c.medium == m for c in plan.cases) for m in ("whiteboard", "phone")
        },
        "physical_capture_verified": False,
        "release_validated": False,
        "limitation": "Separate measurements and human judgments; no invented aggregate acceptance threshold. Capture origin and freeze chronology require independent verification.",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "capture", "review", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    args = parser.parse_args()
    records = []
    for name, schema in (
        ("plan", VisualPlan),
        ("capture", VisualCapture),
        ("review", VisualReview),
    ):
        with getattr(args, name).open("rb") as source:
            data = source.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("visual_record_size_limit")
        records.append(schema.model_validate_json(data))
    result = score_visual(*records)
    result["image_artifacts"] = verify_images(args.image_root, records[0], records[1])
    result["record_sha256"] = [digest(record) for record in records]
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    # Successful report generation is deliberately not a physical/acceptance pass.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
