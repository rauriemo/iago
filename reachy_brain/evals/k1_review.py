"""Score declared independent human reviews; never infer semantic grades from capture."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from reachy_brain.evals.k1_corpus import load


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FactLabels(StrictModel):
    question: str = Field(min_length=1, max_length=128)
    facts: list[str] = Field(min_length=1, max_length=32)
    support_ids: list[str] = Field(min_length=1, max_length=8)


class AnswerGrade(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    supported_correct: bool
    abstained: bool
    factual_claims: bool
    citations_complete: bool
    citations_valid: bool
    unsupported_assertions: int = Field(ge=0, le=1000)
    fabricated_citations: int = Field(ge=0, le=1000)
    explicit_absence: bool
    cross_project_leakage: bool
    rationale: str = Field(min_length=1, max_length=4000)


class HumanReview(StrictModel):
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_at: str = Field(min_length=1, max_length=128)
    independent_human_authored: bool
    labels: list[FactLabels] = Field(min_length=30, max_length=30)
    grades: list[AnswerGrade] = Field(min_length=55, max_length=55)


def read_bounded(path):
    with Path(path).open("rb") as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("review_input_size_limit")
    return raw


def review_template(capture_raw):
    report = json.loads(capture_raw)
    result = next(
        r for r in report["results"] if r["scenario"] == "K1-ASTRA-CANDIDATE-ANSWER-CAPTURE"
    )
    answers = result["measurements"]["answers"]
    if result["status"] != "pass" or len(answers) != 55 or len({a["id"] for a in answers}) != 55:
        raise ValueError("successful_complete_capture_required")
    _, _, questions = load()
    return {
        "capture_sha256": hashlib.sha256(capture_raw).hexdigest(),
        "reviewer": "",
        "reviewed_at": "",
        "independent_human_authored": None,
        "labels": [
            {"question": q["id"], "facts": [], "support_ids": []} for q in questions["answerable"]
        ],
        "grades": [
            {
                **{field: None for field in AnswerGrade.model_fields},
                "id": a["id"],
                "answer_sha256": a["sha256"],
                "rationale": "",
            }
            for a in answers
        ],
    }


def score_review(capture_raw, review_raw):
    review = HumanReview.model_validate(json.loads(review_raw))
    if not review.independent_human_authored or not review.reviewer.strip():
        raise ValueError("independent_human_review_required")
    if hashlib.sha256(capture_raw).hexdigest() != review.capture_sha256:
        raise ValueError("capture_hash_mismatch")
    report = json.loads(capture_raw)
    results = [r for r in report["results"] if r["scenario"] == "K1-ASTRA-CANDIDATE-ANSWER-CAPTURE"]
    if len(results) != 1 or results[0]["status"] != "pass":
        raise ValueError("successful_complete_capture_required")
    measurements = results[0]["measurements"]
    manifest, _, questions = load()
    if measurements["model"] != "gpt-6-astra" or measurements["fixture"] != manifest:
        raise ValueError("capture_model_or_fixture_mismatch")
    expected = {
        f"{q['id']}:{i}": (q["project"], query, "answerable")
        for q in questions["answerable"]
        for i, query in enumerate(q["variants"])
    }
    expected.update({q["id"]: (q["project"], q["question"], "absent") for q in questions["absent"]})
    answers = measurements["answers"]
    by_id = {a["id"]: a for a in answers}
    grades = {g.id: g for g in review.grades}
    if (
        len(answers) != 55
        or len(by_id) != 55
        or set(by_id) != set(expected)
        or set(grades) != set(expected)
    ):
        raise ValueError("complete_unique_case_set_required")
    labels = {label.question: label for label in review.labels}
    if set(labels) != {q["id"] for q in questions["answerable"]}:
        raise ValueError("complete_unique_human_labels_required")
    for q in questions["answerable"]:
        label = labels[q["id"]]
        if not all(fact.strip() and len(fact) <= 4000 for fact in label.facts):
            raise ValueError("nonempty_bounded_human_facts_required")
        if set(label.support_ids) != {s["id"] for s in q["supports"]}:
            raise ValueError("review_supports_differ_from_frozen_corpus")
    failures, correct, absent = [], 0, 0
    for case_id, (project, query, kind) in expected.items():
        answer, grade = by_id[case_id], grades[case_id]
        content = {k: v for k, v in answer.items() if k != "sha256"}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        if digest != answer["sha256"] or digest != grade.answer_sha256:
            raise ValueError("answer_hash_mismatch")
        if (answer["project"], answer["query"], answer["kind"]) != (project, query, kind):
            raise ValueError("capture_case_mismatch")
        if not grade.rationale.strip():
            raise ValueError("review_rationale_required")
        if kind == "answerable" and grade.supported_correct and grade.abstained:
            raise ValueError("abstention_cannot_be_correct_answer")
        if kind == "answerable" and grade.supported_correct and not grade.factual_claims:
            raise ValueError("correct_factual_answer_requires_claims")
        if kind == "answerable":
            correct += grade.supported_correct
        else:
            absent += grade.supported_correct and grade.explicit_absence
        if (
            grade.unsupported_assertions
            or grade.fabricated_citations
            or grade.cross_project_leakage
        ):
            failures.append(case_id + ": unsupported, fabricated or cross-project content")
        if not grade.citations_valid or (grade.factual_claims and not grade.citations_complete):
            failures.append(case_id + ": citation validity/coverage")
        if grade.factual_claims and not answer["evidence"]:
            failures.append(case_id + ": factual answer has no displayed evidence")
    if correct < 41:
        failures.append("supported correctness below 41/45")
    if absent != 10:
        failures.append("explicit supported absence below 10/10")
    return {
        "status": "fail" if failures else "pass",
        "failures": failures,
        "supported_correct": correct,
        "answerable": 45,
        "explicit_absence": absent,
        "absent_questions": 10,
        "capture_sha256": review.capture_sha256,
        "review_sha256": hashlib.sha256(review_raw).hexdigest(),
        "reviewer": review.reviewer,
        "reviewed_at": review.reviewed_at,
        "scope": "K1 candidate answer/citation/absence scoring from declared independent human review; not full K1, retrieval or physical qualification",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Create an ungraded independent K1 review template"
    )
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    template = review_template(read_bounded(args.capture))
    # Never overwrite a review that may contain user judgments.
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(template, target, indent=2, ensure_ascii=False)
        target.write("\n")


if __name__ == "__main__":
    main()
