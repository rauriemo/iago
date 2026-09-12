"""Synthetic captures, grades and recording declarations; no real human or PC qualification."""

import hashlib
import json

import pytest
from test_k1_review_scoring import encoded, synthetic_review_data
from test_physical_k1 import evaluate_pc_documents

from reachy_brain.evals.k1_pc_bundle import K1PCBundle, PCQueryReview, score_pc_bundle


@pytest.fixture
def synthetic_review():
    return synthetic_review_data()


def bundle_files(root, capture, grades, case):
    if case == "quality":
        grades["grades"][0]["unsupported_assertions"] = 1
    answers = capture["results"][0]["measurements"]["answers"]
    queries = [
        dict(
            id=a["id"],
            answer_sha256=a["sha256"],
            input_mode="spoken" if i < 10 else "typed",
            started=float(i),
            completed=float(i + 1),
        )
        for i, a in enumerate(answers)
    ]
    if case == "spoken":
        queries[0]["input_mode"] = "typed"
    elif case == "duplicate":
        queries[-1] = queries[0]
    elif case == "answer":
        queries[0]["answer_sha256"] = "0" * 64
    elif case == "interval":
        queries[0]["completed"] = 56.0
    session = dict(profile="pc", origin="reported-real", duration=55.0, queries=queries)
    if case == "synthetic":
        session["origin"] = "synthetic"
    values = {
        "capture": encoded(capture),
        "answer_review": encoded(grades),
        "plan": b"Synthetic plan",
        "recording": b"Synthetic recording",
        "session": encoded(session),
    }
    bundle = {}
    for name, raw in values.items():
        (root / (name + ".json")).write_bytes(raw)
        bundle[name] = dict(file=name + ".json", sha256=hashlib.sha256(raw).hexdigest())
    review = dict(
        reviewer="synthetic-private-reviewer",
        artifacts={k: v["sha256"] for k, v in bundle.items()},
        frozen_at=1.0,
        recorded_at=2.0,
        reviewed_at=58.0,
        reviewed_query_ids=[a["id"] for a in answers],
    )
    for name in PCQueryReview.model_fields:
        if name not in review:
            review[name] = True
    if case == "chronology":
        review["reviewed_at"] = 56.0
    elif case == "review_ids":
        review["reviewed_query_ids"][-1] = review["reviewed_query_ids"][0]
    elif case == "binding":
        review["artifacts"]["capture"] = "0" * 64
    elif case == "controls":
        review["reindex_errors_and_removal_reviewed"] = False
    raw = encoded(review)
    (root / "ui-review.json").write_bytes(raw)
    bundle["ui_review"] = dict(file="ui-review.json", sha256=hashlib.sha256(raw).hexdigest())
    if case == "recording":
        (root / "recording.json").write_bytes(b"Changed")
    return K1PCBundle.model_validate(bundle)


@pytest.mark.features("K1", "D4", "C4")
@pytest.mark.scenario("K1-PC-BOUND-QUERY-RECORDS")
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "spoken",
        "quality",
        "duplicate",
        "answer",
        "interval",
        "synthetic",
        "chronology",
        "review_ids",
        "binding",
        "controls",
        "recording",
    ],
)
def test_query_binding_and_original_quality_gates(tmp_path, synthetic_review, case):
    bundle = bundle_files(tmp_path, *synthetic_review, case)
    if case not in {"valid", "spoken", "quality"}:
        with pytest.raises(ValueError):
            score_pc_bundle(tmp_path, bundle)
        return
    result = score_pc_bundle(tmp_path, bundle)
    assert result["status"] == ("review_required" if case == "valid" else "fail")
    assert result["spoken_queries"] == (9 if case == "spoken" else 10)
    assert result["query_count"] == 55
    assert not result["acceptance_pass"] and not result["physical_qualification"]
    assert "synthetic-private-reviewer" not in json.dumps(result)


@pytest.mark.features("K1", "D4", "C4")
@pytest.mark.scenario("K1-PC-ENTRYPOINT-EVIDENCE")
@pytest.mark.parametrize("case", ["valid", "spoken", "missing"])
def test_entrypoint_outcomes_and_measurements(tmp_path, monkeypatch, synthetic_review, case):
    monkeypatch.delenv("IAGO_PC_K1_MANIFEST", raising=False)
    properties = {}
    if case == "missing":
        with pytest.raises(pytest.skip.Exception, match="IAGO_PC_K1_MANIFEST"):
            evaluate_pc_documents(properties.__setitem__)
        assert not properties
        return
    bundle = bundle_files(tmp_path, *synthetic_review, case)
    manifest = dict(
        fixture_kind="physical-pc-independent-recording",
        profile="pc",
        operator="synthetic",
        application_revision="synthetic",
        devices="synthetic",
        bundle=bundle.model_dump(),
    )
    raw = encoded(manifest)
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    monkeypatch.setenv("IAGO_PC_K1_MANIFEST", str(path))
    if case == "spoken":
        with pytest.raises(AssertionError, match="ten spoken"):
            evaluate_pc_documents(properties.__setitem__)
    else:
        evaluate_pc_documents(properties.__setitem__)
    assert properties["measurements"]["manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    assert not properties["measurements"]["physical_qualification"]
