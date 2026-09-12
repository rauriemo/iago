"""V9 captured-run scoring. Evidence review is required; this does not capture a PC."""

import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Reference(StrictRecord):
    frame_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    captured_at: float = Field(ge=0)
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScreenCase(StrictRecord):
    id: str = Field(min_length=1, max_length=128)
    category: Literal[
        "current",
        "history_1",
        "history_5",
        "history_9",
        "crop",
        "camera_confusion",
        "pin_expiry",
        "source_change",
        "unreadable",
        "blank",
    ]
    picker: Literal["monitor", "window", "tab"]
    references: list[Reference] = Field(min_length=1, max_length=8)
    labels: dict[str, str] = Field(max_length=100)


class ScreenPlan(StrictRecord):
    version: Literal[1]
    cases: list[ScreenCase] = Field(min_length=14, max_length=200)
    # All three choices must be accounted for. A nonempty reason means unavailable.
    unavailable_picker_choices: dict[str, str] = Field(max_length=3)


class ScreenAnswer(StrictRecord):
    case_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=12000)
    citations: list[Reference] = Field(max_length=32)
    # Reviewer extracts verbatim answer spans for the frozen label IDs. Missing is wrong.
    readings: dict[str, str] = Field(max_length=100)


class ScreenCapture(StrictRecord):
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: Literal["gpt-6-astra"]
    answers: list[ScreenAnswer] = Field(min_length=14, max_length=200)


class ScreenReview(StrictRecord):
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    independent_human_review: bool
    # Each verdict covers the entire answer, not just the extracted exact-label spans.
    no_invented_text: dict[str, bool] = Field(max_length=200)
    scenario_execution_verified: dict[str, bool] = Field(max_length=200)


def digest(record: BaseModel) -> str:
    """Canonical content binding, independent of JSON indentation/key order."""
    data = json.dumps(
        record.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def score_screen(plan: ScreenPlan, capture: ScreenCapture, review: ScreenReview):
    """Fail malformed/incomplete evidence; return separate measured gate outcomes.

    Caller must verify artifact/image hashes and physical capture provenance. A valid
    synthetic record can test the scorer but can never establish a physical pass.
    """
    cases = {case.id: case for case in plan.cases}
    answers = {answer.case_id: answer for answer in capture.answers}
    if len(cases) != len(plan.cases) or len(answers) != len(capture.answers):
        raise ValueError("duplicate_case_id")
    if set(cases) != set(answers):
        raise ValueError("case_coverage_mismatch")
    if capture.plan_sha256 != digest(plan) or review.capture_sha256 != digest(capture):
        raise ValueError("evidence_binding_mismatch")
    if not review.independent_human_review or not review.reviewer.strip():
        raise ValueError("independent_human_review_required")
    if set(review.no_invented_text) != set(cases) or set(review.scenario_execution_verified) != set(
        cases
    ):
        raise ValueError("review_coverage_mismatch")
    counts = Counter(case.category for case in plan.cases)
    required = {
        "current": 3,
        "history_1": 1,
        "history_5": 1,
        "history_9": 1,
        "crop": 2,
        "camera_confusion": 2,
        "pin_expiry": 1,
        "source_change": 1,
        "unreadable": 1,
        "blank": 1,
    }
    if any(counts[k] < count for k, count in required.items()):
        raise ValueError("required_scenarios_missing")
    offered = {case.picker for case in plan.cases}
    unavailable = plan.unavailable_picker_choices
    if (
        offered & set(unavailable)
        or offered | set(unavailable) != {"monitor", "window", "tab"}
        or any(not reason.strip() or len(reason) > 1000 for reason in unavailable.values())
    ):
        raise ValueError("picker_coverage_invalid")
    label_ids = [label for case in plan.cases for label in case.labels]
    if len(label_ids) < 20 or len(set(label_ids)) != len(label_ids):
        raise ValueError("twenty_unique_labels_required")
    correct = 0
    selected = 0
    valid_citations = 0
    total_citations = 0
    for case in plan.cases:
        answer = answers[case.id]
        if any(
            not k.strip() or len(k) > 128 or not v.strip() or len(v) > 1000
            for k, v in case.labels.items()
        ):
            raise ValueError("invalid_exact_label")
        if case.category in {"blank", "unreadable"} and case.labels:
            raise ValueError("negative_case_has_readable_labels")
        if not set(answer.readings) <= set(case.labels):
            raise ValueError("unknown_reading_label")
        if any(not v or v not in answer.text for v in answer.readings.values()):
            raise ValueError("reading_not_verbatim_answer_span")
        correct += sum(answer.readings.get(k) == v for k, v in case.labels.items())
        expected = {digest(ref) for ref in case.references}
        observed = {digest(ref) for ref in answer.citations}
        if len(expected) != len(case.references):
            raise ValueError("duplicate_reference")
        selected += expected == observed and bool(answer.citations)
        valid_citations += sum(digest(ref) in expected for ref in answer.citations)
        total_citations += len(answer.citations)
    exact_pass = correct * 10 >= len(label_ids) * 9
    source_pass = selected == len(cases)
    provenance_pass = total_citations > 0 and valid_citations == total_citations
    negative_pass = all(
        review.no_invented_text[c.id] for c in plan.cases if c.category in {"unreadable", "blank"}
    )
    execution_pass = all(review.scenario_execution_verified.values())
    return {
        "cases": len(cases),
        "labels": len(label_ids),
        "exact_labels_correct": correct,
        "exact_label_fraction": correct / len(label_ids),
        "exact_labels_pass": exact_pass,
        "correct_reference_cases": selected,
        "source_selection_pass": source_pass,
        "citations": total_citations,
        "valid_citations": valid_citations,
        "citation_provenance_pass": provenance_pass,
        "negative_text_pass": negative_pass,
        "reviewed_execution_pass": execution_pass,
        "passes_scored_checks": all(
            (exact_pass, source_pass, provenance_pass, negative_pass, execution_pass)
        ),
        "physical_capture_verified": False,
    }


def main():
    """Score bounded local records, without provider calls or a physical-pass claim."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--image-root", type=Path, help="Private directory of retained <sha256>.jpg artifacts"
    )
    args = parser.parse_args()
    records = []
    for path, schema in (
        (args.plan, ScreenPlan),
        (args.capture, ScreenCapture),
        (args.review, ScreenReview),
    ):
        with path.open("rb") as source:
            data = source.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("screen_record_size_limit")
        records.append(schema.model_validate_json(data))
    result = score_screen(*records)
    result["image_artifacts_verified"] = False
    if args.image_root is not None:
        result["image_artifacts"] = verify_images(args.image_root, records[0], records[1])
        result["image_artifacts_verified"] = True
    result["record_sha256"] = [digest(record) for record in records]
    # Keep content/identity out of the score file. Refuse to replace earlier evidence.
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    return 0 if result["passes_scored_checks"] else 2


def verify_images(root: Path, plan: ScreenPlan, capture: ScreenCapture):
    """Check exact retained JPEG/PNG bytes, not screenshot origin or model correctness."""
    if capture.plan_sha256 != digest(plan):
        raise ValueError("evidence_binding_mismatch")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("image_root_not_directory")
    hashes = {ref.image_sha256 for case in plan.cases for ref in case.references}
    hashes.update(ref.image_sha256 for answer in capture.answers for ref in answer.citations)
    total = 0
    for expected in sorted(hashes):
        # Recheck even for callers that mutate already-validated Pydantic records.
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError("invalid_image_hash")
        candidates = [root / f"{expected}{ext}" for ext in (".jpg", ".png")]
        existing = [candidate for candidate in candidates if candidate.exists()]
        if not existing:
            raise FileNotFoundError("image_artifact_missing")
        if len(existing) != 1:
            raise ValueError("one_image_artifact_required")
        path = existing[0].resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("image_outside_artifact_root")
        with path.open("rb") as source:
            data = source.read(min(64 * 1024 * 1024, 512 * 1024 * 1024 - total) + 1)
        total += len(data)
        if len(data) > 64 * 1024 * 1024 or total > 512 * 1024 * 1024:
            raise ValueError("image_artifact_byte_limit")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("image_artifact_hash_mismatch")
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != ("PNG" if path.suffix == ".png" else "JPEG")
                or image.width * image.height > 20_000_000
                or max(image.size) > 8192
            ):
                raise ValueError("invalid_retained_image_format")
            image.load()
    return {"unique_images": len(hashes), "bytes": total}


if __name__ == "__main__":
    raise SystemExit(main())
