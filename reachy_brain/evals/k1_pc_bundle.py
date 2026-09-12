"""Bind reviewed PC query records to K1 answer scoring; declarations are not device proof."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .k1_review import score_review
from .robot_motion_bundle import Artifact, read_bound
from .screen_grounding import StrictRecord


class K1PCBundle(StrictRecord):
    capture: Artifact
    answer_review: Artifact
    plan: Artifact
    recording: Artifact
    session: Artifact
    ui_review: Artifact


class QueryRecord(StrictRecord):
    id: str = Field(min_length=1, max_length=128)
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_mode: Literal["typed", "spoken"]
    started: float = Field(ge=0)
    completed: float = Field(ge=0)


class PCQuerySession(StrictRecord):
    profile: Literal["pc"]
    origin: Literal["reported-real"]
    duration: float = Field(gt=0, le=14400)
    queries: list[QueryRecord] = Field(min_length=55, max_length=55)


class PCQueryReview(StrictRecord):
    reviewer: str = Field(min_length=1, max_length=128)
    artifacts: dict[str, str]
    frozen_at: float = Field(ge=0)
    recorded_at: float = Field(ge=0)
    reviewed_at: float = Field(ge=0)
    reviewed_query_ids: list[str] = Field(min_length=55, max_length=55)
    independent_human_authored: Literal[True]
    recording_origin_and_alignment_reviewed: Literal[True]
    all_queries_through_real_ui_reviewed: Literal[True]
    spoken_input_and_transcription_reviewed: Literal[True]
    displayed_answers_and_snippets_reviewed: Literal[True]
    project_configuration_selection_and_progress_reviewed: Literal[True]
    reindex_errors_and_removal_reviewed: Literal[True]
    originals_unchanged_on_removal_reviewed: Literal[True]
    durable_index_and_transient_context_distinction_reviewed: Literal[True]


def score_pc_bundle(root: Path, bundle: K1PCBundle):
    capture = read_bound(root, bundle.capture, limit=8 * 1024 * 1024)
    quality = score_review(capture, read_bound(root, bundle.answer_review, limit=8 * 1024 * 1024))
    session = PCQuerySession.model_validate_json(read_bound(root, bundle.session))
    review = PCQueryReview.model_validate_json(read_bound(root, bundle.ui_review))
    expected_hashes = {
        name: value["sha256"] for name, value in bundle.model_dump(exclude={"ui_review"}).items()
    }
    if review.artifacts != expected_hashes or not review.reviewer.strip():
        raise ValueError("pc_query_review_binding")
    if not (
        review.frozen_at < review.recorded_at
        and review.recorded_at + session.duration <= review.reviewed_at
    ):
        raise ValueError("pc_query_review_chronology")
    result = next(
        r
        for r in json.loads(capture)["results"]
        if r["scenario"] == "K1-ASTRA-CANDIDATE-ANSWER-CAPTURE"
    )
    answers = {a["id"]: a["sha256"] for a in result["measurements"]["answers"]}
    ids = [q.id for q in session.queries]
    if (
        len(set(ids)) != 55
        or set(ids) != set(answers)
        or len(set(review.reviewed_query_ids)) != 55
        or set(review.reviewed_query_ids) != set(answers)
    ):
        raise ValueError("pc_query_complete_unique_cases")
    for query in session.queries:
        if query.answer_sha256 != answers[query.id]:
            raise ValueError("pc_query_answer_binding")
        if not query.started < query.completed <= session.duration:
            raise ValueError("pc_query_interval")
    spoken = sum(q.input_mode == "spoken" for q in session.queries)
    failures = list(quality["failures"])
    if spoken < 10:
        failures.append("fewer than ten spoken queries")
    sizes = {
        name: read_bound(root, getattr(bundle, name), limit=limit, retain=False)
        for name, limit in (("plan", 8 * 1024 * 1024), ("recording", 512 * 1024 * 1024))
    }
    # Reviewer identity and free-form judgments remain in the private bound artifacts.
    quality = {k: v for k, v in quality.items() if k not in {"reviewer", "reviewed_at"}}
    return {
        "status": "fail" if failures else "review_required",
        "failures": failures,
        "query_count": len(ids),
        "spoken_queries": spoken,
        "quality": quality,
        "artifacts": bundle.model_dump(),
        "supporting_artifact_bytes": sizes,
        "acceptance_pass": False,
        "physical_qualification": False,
        "scope": "Bound answer grades and declared UI/spoken-query review only. Independently substantiate actual device input, displayed answers, controls, plan freeze and recording origin. Prior direct-controller captures do not establish a PC UI session. Full K1 and robot gates remain separate.",
    }
