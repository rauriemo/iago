"""Extract visual answers from an opt-in transcript export without inventing readings."""

import hashlib
import json
from pathlib import Path

from pydantic import Field

from reachy_brain.evals.screen_grounding import ScreenAnswer, ScreenCapture, ScreenPlan
from reachy_brain.evals.visual_grounding import (
    Reference,
    StrictRecord,
    VisualAnswer,
    VisualCapture,
    VisualPlan,
    digest,
)


class CaseMapping(StrictRecord):
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Case ID -> exact saved assistant entry ID. Never reuse one answer for two cases.
    entries: dict[str, str] = Field(min_length=10, max_length=200)


def extract_answers(plan: VisualPlan | ScreenPlan, mapping: CaseMapping, transcript_bytes: bytes):
    answer_type = ScreenAnswer if isinstance(plan, ScreenPlan) else VisualAnswer
    if len(transcript_bytes) > 32 * 1024 * 1024:
        raise ValueError("transcript_export_size_limit")
    if mapping.transcript_sha256 != hashlib.sha256(transcript_bytes).hexdigest():
        raise ValueError("transcript_binding_mismatch")
    if mapping.plan_sha256 != digest(plan):
        raise ValueError("plan_binding_mismatch")
    case_ids = [case.id for case in plan.cases]
    if len(set(case_ids)) != len(case_ids) or set(mapping.entries) != set(case_ids):
        raise ValueError("case_coverage_mismatch")
    if len(set(mapping.entries.values())) != len(mapping.entries):
        raise ValueError("reused_answer_entry")
    export = json.loads(transcript_bytes)
    if (
        not isinstance(export, dict)
        or type(export.get("format_version")) is not int
        or export["format_version"] != 1
        or not isinstance(export.get("session"), str)
        or not 0 < len(export["session"]) <= 128
    ):
        raise ValueError("unsupported_transcript_export")
    rows = export.get("entries")
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("invalid_transcript_entries")
    indexed = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("entry"), str)
            or not 0 < len(row["entry"]) <= 128
        ):
            raise ValueError("invalid_transcript_entry")
        if row["entry"] in indexed:
            raise ValueError("duplicate_transcript_entry")
        indexed[row["entry"]] = row
    answers, provenance = [], []
    for case_id in case_ids:
        entry_id = mapping.entries[case_id]
        row = indexed.get(entry_id)
        if row is None or row.get("role") != "assistant" or row.get("kind") != "heard":
            raise ValueError("recorded_assistant_answer_required")
        meta = row.get("metadata")
        if not isinstance(meta, dict) or meta.get("evidence_truncated", False) is not False:
            raise ValueError("incomplete_answer_evidence")
        refs = meta.get("evidence_refs")
        if not isinstance(refs, list) or len(refs) > 32:
            raise ValueError("missing_answer_evidence")
        citations = []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("kind") != "visual":
                raise ValueError("nonvisual_answer_evidence")
            if ref.get("capture_time_known") is not True:
                raise ValueError("uncertain_capture_time")
            citation = Reference(
                frame_id=ref.get("id"),
                source_id=ref.get("source_id"),
                captured_at=ref.get("captured"),
                image_sha256=ref.get("image_sha256"),
            )
            if citation not in citations:
                citations.append(citation)
        answers.append(
            answer_type(
                case_id=case_id, text=row.get("text"), citations=citations, readings={}
            ).model_dump()
        )
        provenance.append(
            {
                "case_id": case_id,
                "entry_id": entry_id,
                "created": row.get("created"),
                "metadata": meta,
            }
        )
    return {
        "evaluation_kind": "screen" if isinstance(plan, ScreenPlan) else "visual",
        "plan_sha256": digest(plan),
        "transcript_sha256": mapping.transcript_sha256,
        "session": export["session"],
        "answers": answers,
        "provenance": provenance,
        "status": "requires_capture_metadata_and_independent_review",
        "limitation": "Heard text only. Empty readings need human extraction; original metadata retains interruption and crop evidence. Model identity, run chronology and physical execution are not established by this export.",
    }


def validate_capture_transcript(
    plan: ScreenPlan | VisualPlan,
    mapping: CaseMapping,
    transcript_bytes: bytes,
    capture: ScreenCapture | VisualCapture,
):
    """Bind reviewed text/citations to saved answers; readings remain separately reviewed."""
    if isinstance(plan, ScreenPlan) != isinstance(capture, ScreenCapture):
        raise ValueError("capture_plan_kind_mismatch")
    extracted = extract_answers(plan, mapping, transcript_bytes)
    if capture.plan_sha256 != extracted["plan_sha256"]:
        raise ValueError("capture_plan_binding_mismatch")
    recorded = {answer["case_id"]: answer for answer in extracted["answers"]}
    if len(capture.answers) != len(recorded) or {a.case_id for a in capture.answers} != set(
        recorded
    ):
        raise ValueError("capture_case_coverage_mismatch")
    for answer in capture.answers:
        original = recorded[answer.case_id]
        if (
            answer.text != original["text"]
            or [r.model_dump() for r in answer.citations] != original["citations"]
        ):
            raise ValueError("capture_answer_differs_from_transcript")
    response_ids = set()
    for provenance in extracted["provenance"]:
        metadata = provenance["metadata"]
        responses = metadata.get("model_responses")
        if (
            metadata.get("model_responses_truncated", False) is not False
            or not isinstance(responses, list)
            or not 1 <= len(responses) <= 32
        ):
            raise ValueError("capture_model_provenance_incomplete")
        for response in responses:
            if (
                not isinstance(response, dict)
                or set(response) != {"response_id", "requested_model", "reported_model"}
                or any(not isinstance(v, str) or not 0 < len(v) <= 128 for v in response.values())
                or response["requested_model"] != capture.model
                or response["reported_model"] != capture.model
            ):
                raise ValueError("capture_model_provenance_mismatch")
            if response["response_id"] in response_ids:
                raise ValueError("capture_response_identity_reused")
            response_ids.add(response["response_id"])
    return {
        "transcript_sha256": mapping.transcript_sha256,
        "mapping_sha256": digest(mapping),
        "recorded_answers": len(recorded),
        "recorded_model_responses": len(response_ids),
        "model_metadata_consistent": True,
        "physical_capture_verified": False,
    }


validate_screen_transcript = validate_capture_transcript


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("visual", "screen"), default="visual")
    for name in ("plan", "mapping", "transcript", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    records = []
    for path, cap in (
        (args.plan, 1024 * 1024),
        (args.mapping, 1024 * 1024),
        (args.transcript, 32 * 1024 * 1024),
    ):
        with path.open("rb") as source:
            data = source.read(cap + 1)
        if len(data) > cap:
            raise ValueError("capture_input_size_limit")
        records.append(data)
    result = extract_answers(
        (ScreenPlan if args.kind == "screen" else VisualPlan).model_validate_json(records[0]),
        CaseMapping.model_validate_json(records[1]),
        records[2],
    )
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
