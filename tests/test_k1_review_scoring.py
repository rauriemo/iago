"""Synthetic reviews test scoring mechanics; none is a real human qualification."""

import hashlib
import json

import pytest

from reachy_brain.evals.k1_corpus import load
from reachy_brain.evals.k1_review import review_template, score_review


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


@pytest.fixture
def synthetic_review():
    manifest, _, questions = load()
    cases = [
        (f"{q['id']}:{i}", q["project"], query, "answerable")
        for q in questions["answerable"]
        for i, query in enumerate(q["variants"])
    ]
    cases += [(q["id"], q["project"], q["question"], "absent") for q in questions["absent"]]
    answers, grades = [], []
    for case_id, project, query, kind in cases:
        answer = dict(
            id=case_id,
            project=project,
            query=query,
            kind=kind,
            answer="Synthetic scorer fixture, not actual model output.",
            evidence=[{"synthetic": True}],
        )
        answer["sha256"] = hashlib.sha256(encoded(answer)).hexdigest()
        answers.append(answer)
        grades.append(
            dict(
                id=case_id,
                answer_sha256=answer["sha256"],
                supported_correct=True,
                abstained=kind == "absent",
                factual_claims=kind == "answerable",
                citations_complete=True,
                citations_valid=True,
                unsupported_assertions=0,
                fabricated_citations=0,
                explicit_absence=kind == "absent",
                cross_project_leakage=False,
                rationale="Synthetic arithmetic fixture only.",
            )
        )
    capture = {
        "results": [
            {
                "scenario": "K1-ASTRA-CANDIDATE-ANSWER-CAPTURE",
                "status": "pass",
                "measurements": {"model": "gpt-6-astra", "fixture": manifest, "answers": answers},
            }
        ]
    }
    review = dict(
        capture_sha256=hashlib.sha256(encoded(capture)).hexdigest(),
        reviewer="SYNTHETIC TEST - not a human review",
        reviewed_at="synthetic",
        independent_human_authored=True,
        labels=[
            dict(
                question=q["id"],
                facts=["Synthetic test label"],
                support_ids=[s["id"] for s in q["supports"]],
            )
            for q in questions["answerable"]
        ],
        grades=grades,
    )
    return capture, review


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-REVIEW-CORRECTNESS-THRESHOLD")
@pytest.mark.parametrize("correct,expected", [(40, "fail"), (41, "pass"), (45, "pass")])
def test_original_correctness_threshold_and_abstentions(synthetic_review, correct, expected):
    capture, review = synthetic_review
    for grade in review["grades"][correct:45]:
        grade.update(supported_correct=False, abstained=True, factual_claims=False)
    result = score_review(encoded(capture), encoded(review))
    assert result["status"] == expected and result["supported_correct"] == correct


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-REVIEW-ZERO-TOLERANCE-GATES")
@pytest.mark.parametrize(
    "case,field,value",
    [
        (0, "citations_complete", False),
        (0, "citations_valid", False),
        (0, "unsupported_assertions", 1),
        (0, "fabricated_citations", 1),
        (45, "cross_project_leakage", True),
        (45, "explicit_absence", False),
        (45, "supported_correct", False),
    ],
)
def test_single_violation_fails_otherwise_perfect_review(synthetic_review, case, field, value):
    capture, review = synthetic_review
    review["grades"][case][field] = value
    assert score_review(encoded(capture), encoded(review))["status"] == "fail"


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-REVIEW-BINDING-COMPLETENESS")
@pytest.mark.parametrize(
    "change",
    [
        "capture",
        "answer_hash",
        "duplicate",
        "missing",
        "automated",
        "empty_labels",
        "coerced_grade",
        "contradictory",
    ],
)
def test_changed_incomplete_or_invalid_reviews_are_rejected(synthetic_review, change):
    capture, review = synthetic_review
    if change == "capture":
        capture["changed"] = True
    elif change == "answer_hash":
        review["grades"][0]["answer_sha256"] = "0" * 64
    elif change == "duplicate":
        review["grades"][-1] = review["grades"][0]
    elif change == "missing":
        review["grades"].pop()
    elif change == "automated":
        review["independent_human_authored"] = False
    elif change == "empty_labels":
        review["labels"][0]["facts"] = [" "]
    elif change == "coerced_grade":
        review["grades"][0]["supported_correct"] = "true"
    else:
        review["grades"][0]["abstained"] = True
    with pytest.raises(ValueError):
        score_review(encoded(capture), encoded(review))


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-REVIEW-TEMPLATE-IS-UNGRADED")
def test_template_preserves_bindings_without_inventing_judgments(synthetic_review):
    capture, _ = synthetic_review
    raw = encoded(capture)
    template = review_template(raw)
    assert len(template["labels"]) == 30 and len(template["grades"]) == 55
    assert all(not label["facts"] and not label["support_ids"] for label in template["labels"])
    assert all(grade["supported_correct"] is None for grade in template["grades"])
    assert template["capture_sha256"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        score_review(raw, encoded(template))
