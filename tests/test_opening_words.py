"""Synthetic text records test scoring only, never physical interruption quality."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from reachy_brain.evals.opening_words import (
    OpeningCase,
    OpeningMapping,
    OpeningPlan,
    OpeningReview,
    digest,
    score_openings,
    validate_opening_review,
)

pytestmark = [
    pytest.mark.features("C1", "C2", "D5"),
    pytest.mark.scenario("INTERRUPTION-OPENING-WORDS-SCORER"),
]


def records():
    plan = OpeningPlan(
        version=1,
        cases=[
            OpeningCase(
                id=str(i),
                provider=("openai", "elevenlabs", "fallback")[i % 3],
                expected_opening="Wait, don't",
            )
            for i in range(20)
        ],
    )
    export = {
        "format_version": 1,
        "session": "synthetic",
        "entries": [
            {
                "entry": str(i),
                "role": "user",
                "kind": "speech",
                "text": "WAIT—don’t change it.",
                "metadata": {"recognition_id": "speech-" + str(i)},
            }
            for i in range(20)
        ],
    }
    mapping = OpeningMapping(
        plan_sha256=digest(plan),
        transcript_sha256="0" * 64,
        entries={str(i): str(i) for i in range(20)},
    )
    return plan, mapping, export


def bind(mapping, export):
    raw = json.dumps(export).encode()
    mapping.transcript_sha256 = hashlib.sha256(raw).hexdigest()
    return raw


def test_missing_and_dropped_openings_are_counted_without_physical_claim():
    plan, mapping, export = records()
    full = score_openings(plan, mapping, bind(mapping, export))
    assert full["preserved"] == full["total"] == 20
    assert full["all_openings_preserved"]
    export["entries"][0]["text"] = "don't change it."
    export["entries"].pop()
    result = score_openings(plan, mapping, bind(mapping, export))
    assert result["preserved"] == 18 and result["total"] == 20
    assert not result["all_openings_preserved"]
    assert result["trials"][0]["status"] == "opening_mismatch"
    assert result["trials"][-1]["status"] == "missing_recognition"
    assert result["physical_capture_verified"] is False
    assert "change it" not in json.dumps(result)


@pytest.mark.parametrize(
    "change,error",
    [
        ("typed", "speech_recognition_entry_required"),
        ("reuse", "reused_recognition_entry"),
        ("identity", "reused_recognition_identity"),
        ("missing_provider", "provider_coverage_missing"),
        ("empty", "opening_words_required"),
        ("changed", "evidence_binding_mismatch"),
    ],
)
def test_invalid_binding_and_labels_cannot_pass(change, error):
    plan, mapping, export = records()
    if change == "typed":
        export["entries"][0]["kind"] = "typed"
    if change == "reuse":
        mapping.entries["0"] = "1"
    if change == "identity":
        export["entries"][0]["metadata"]["recognition_id"] = "speech-1"
    if change == "missing_provider":
        for case in plan.cases:
            case.provider = "openai"
    if change == "empty":
        plan.cases[0].expected_opening = "..."
    mapping.plan_sha256 = digest(plan)
    raw = bind(mapping, export)
    if change == "changed":
        raw += b" "
    with pytest.raises(ValueError, match=error):
        score_openings(plan, mapping, raw)


@pytest.mark.parametrize("change", [None, "binding", "omit", "provider", "unreviewed", "freeze"])
def test_review_binds_all_recorded_interruptions(change):
    plan, mapping, export = records()
    bind(mapping, export)
    trials = [SimpleNamespace(id=c.id, provider=c.provider, action="spoken") for c in plan.cases]
    raw = b"synthetic acoustic manifest"
    review = OpeningReview(
        mapping_sha256=digest(mapping),
        acoustic_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        reviewer="synthetic",
        independent_human_review=True,
        labels_frozen_before_run=True,
        recording_trials={c.id: c.id for c in plan.cases},
        utterance_and_overlap_verified={c.id: True for c in plan.cases},
    )
    if change == "binding":
        review.mapping_sha256 = "0" * 64
    if change == "omit":
        trials.append(SimpleNamespace(id="omitted", provider="openai", action="spoken"))
    if change == "provider":
        trials[0].provider = "elevenlabs"
    if change == "unreviewed":
        review.utterance_and_overlap_verified["0"] = False
    if change == "freeze":
        review.labels_frozen_before_run = False
    if change:
        with pytest.raises(ValueError):
            validate_opening_review(plan, mapping, review, raw, trials)
    else:
        assert validate_opening_review(plan, mapping, review, raw, trials) == trials
