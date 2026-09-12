"""Synthetic exports exercise capture mapping, not physical scenario execution."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from test_visual_grounding import records

from reachy_brain.core.conversation import Conversation
from reachy_brain.evals.visual_capture import CaseMapping, extract_answers
from reachy_brain.evals.visual_grounding import digest

pytestmark = [
    pytest.mark.features("C9", "V2", "V4", "V8"),
    pytest.mark.scenario("VISUAL-CAPTURE-TRANSCRIPT-MAPPING"),
]


def fixture(plan_kind="visual"):
    if plan_kind == "screen":
        from test_screen_grounding import records as screen_records

        plan, capture, _ = screen_records()
    else:
        plan, capture, _ = records()
    rows = []
    for answer in capture.answers:
        rows.append(
            {
                "entry": "a" + answer.case_id,
                "role": "assistant",
                "kind": "heard",
                "text": answer.text,
                "created": 12.0,
                "metadata": {
                    "model_responses": [
                        {
                            "response_id": "synthetic-response-" + answer.case_id,
                            "requested_model": "gpt-6-astra",
                            "reported_model": "gpt-6-astra",
                        }
                    ],
                    "generated_text": answer.text + " Unheard continuation.",
                    "interrupted": True,
                    "evidence_refs": [
                        {
                            "kind": "visual",
                            "id": r.frame_id,
                            "source_id": r.source_id,
                            "captured": r.captured_at,
                            "capture_time_known": True,
                            "image_sha256": r.image_sha256,
                            "source_generation": 1,
                            "region": [1, 2, 3, 4],
                        }
                        for r in answer.citations
                    ],
                },
            }
        )
    export = {"format_version": 1, "session": "synthetic", "entries": rows}
    mapping = CaseMapping(
        plan_sha256=digest(plan),
        transcript_sha256="0" * 64,
        entries={c.id: "a" + c.id for c in plan.cases},
    )
    return plan, mapping, export


def bind(mapping, export):
    data = json.dumps(export).encode()
    mapping.transcript_sha256 = hashlib.sha256(data).hexdigest()
    return data


@pytest.mark.features("V9", "D5")
@pytest.mark.scenario("VISUAL-EXPORT-STRUCTURE-VALIDATION")
@pytest.mark.parametrize(
    "change",
    [
        "list",
        "null",
        "boolean_version",
        "empty_session",
        "long_session",
        "empty_entry",
        "long_entry",
    ],
)
def test_export_structure_and_identifiers_are_bounded(change):
    plan, mapping, export = fixture("screen")
    if change == "list":
        export = []
    elif change == "null":
        export = None
    elif change == "boolean_version":
        export["format_version"] = True
    elif change == "empty_session":
        export["session"] = ""
    elif change == "long_session":
        export["session"] = "s" * 129
    else:
        # Even unselected rows must have well-formed bounded identities.
        export["entries"].append({"entry": "" if change == "empty_entry" else "e" * 129})
    data = bind(mapping, export)
    with pytest.raises(
        ValueError, match="(unsupported_transcript_export|invalid_transcript_entry)"
    ):
        extract_answers(plan, mapping, data)


@pytest.mark.features("V9")
@pytest.mark.scenario("SCREEN-MODEL-PROVENANCE-REQUIRED")
@pytest.mark.parametrize(
    "change",
    ["missing", "empty", "reported", "requested", "unknown", "truncated", "reused", "oversized"],
)
def test_screen_model_claim_requires_complete_matching_recorded_provenance(change):
    from reachy_brain.evals.screen_grounding import ScreenCapture
    from reachy_brain.evals.visual_capture import validate_screen_transcript

    plan, mapping, export = fixture("screen")
    metadata = export["entries"][0]["metadata"]
    response = metadata["model_responses"][0]
    if change == "missing":
        metadata.pop("model_responses")
    elif change == "empty":
        metadata["model_responses"] = []
    elif change in {"reported", "requested"}:
        response[change + "_model"] = "another-model"
    elif change == "unknown":
        response.pop("reported_model")
    elif change == "truncated":
        metadata["model_responses_truncated"] = True
    elif change == "reused":
        response["response_id"] = export["entries"][1]["metadata"]["model_responses"][0][
            "response_id"
        ]
    elif change == "oversized":
        response["response_id"] = "r" * 129
    data = bind(mapping, export)
    capture = ScreenCapture(
        plan_sha256=digest(plan),
        model="gpt-6-astra",
        answers=extract_answers(plan, mapping, data)["answers"],
    )
    with pytest.raises(ValueError, match="capture_(model_provenance|response_identity)"):
        validate_screen_transcript(plan, mapping, data, capture)


@pytest.mark.features("V9")
@pytest.mark.scenario("SCREEN-CAPTURE-TRANSCRIPT-BINDING")
@pytest.mark.parametrize("change", [None, "text", "citation", "missing", "plan"])
@pytest.mark.parametrize("plan_kind", ["screen", "visual"])
def test_reviewed_screen_capture_cannot_rewrite_recorded_answers(change, plan_kind):
    from reachy_brain.evals.screen_grounding import ScreenCapture
    from reachy_brain.evals.visual_capture import validate_screen_transcript
    from reachy_brain.evals.visual_grounding import VisualCapture

    plan, mapping, export = fixture(plan_kind)
    data = bind(mapping, export)
    extracted = extract_answers(plan, mapping, data)
    capture = (ScreenCapture if plan_kind == "screen" else VisualCapture)(
        plan_sha256=digest(plan),
        model="gpt-6-astra",
        answers=extracted["answers"],
        **({} if plan_kind == "screen" else {"started_at": plan.frozen_at + 1}),
    )
    if change == "text":
        capture.answers[0].text += " invented continuation"
    elif change == "citation":
        capture.answers[0].citations[0].source_id = "different-source"
    elif change == "missing":
        capture.answers.pop()
    elif change == "plan":
        capture.plan_sha256 = "0" * 64
    if change:
        with pytest.raises(ValueError, match="capture_"):
            validate_screen_transcript(plan, mapping, data, capture)
    else:
        result = validate_screen_transcript(plan, mapping, data, capture)
        assert result["recorded_answers"] == len(plan.cases)
        assert result["transcript_sha256"] == hashlib.sha256(data).hexdigest()
        assert result["physical_capture_verified"] is False


@pytest.mark.features("V9")
@pytest.mark.scenario("SCREEN-CAPTURE-TRANSCRIPT-CLI")
def test_screen_capture_cli_keeps_output_intermediate_and_refuses_overwrite(tmp_path):
    import subprocess
    import sys

    plan, mapping, export = fixture("screen")
    data = bind(mapping, export)
    (tmp_path / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    (tmp_path / "mapping.json").write_text(mapping.model_dump_json(), encoding="utf-8")
    (tmp_path / "session.json").write_bytes(data)
    output = tmp_path / "extracted.json"
    command = [sys.executable, "-m", "reachy_brain.evals.visual_capture", "--kind", "screen"]
    for name, filename in (
        ("plan", "plan.json"),
        ("mapping", "mapping.json"),
        ("transcript", "session.json"),
        ("output", "extracted.json"),
    ):
        command.extend(["--" + name, str(tmp_path / filename)])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    original = output.read_bytes()
    result = json.loads(original)
    assert result["evaluation_kind"] == "screen" and len(result["answers"]) == 14
    assert result["status"] == "requires_capture_metadata_and_independent_review"
    assert "model" not in result and all(a["readings"] == {} for a in result["answers"])
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert repeated.returncode != 0 and "FileExistsError" in repeated.stderr
    assert output.read_bytes() == original


@pytest.mark.features("V9")
@pytest.mark.parametrize("plan_kind", ["visual", "screen"])
def test_extracts_heard_answers_and_original_provenance_without_labels(plan_kind):
    plan, mapping, export = fixture(plan_kind)
    empty = SimpleNamespace(answer_record_epoch=1, answer_metadata={})
    Conversation.record_evidence(empty, 0, [])
    assert empty.answer_metadata == {}
    Conversation.record_evidence(empty, 1, [])
    assert empty.answer_metadata == {"evidence_refs": []}
    export["entries"][-1]["metadata"] = empty.answer_metadata
    result = extract_answers(plan, mapping, bind(mapping, export))
    assert len(result["answers"]) == len(plan.cases)
    assert result["evaluation_kind"] == plan_kind
    assert result["answers"][0]["text"] == export["entries"][0]["text"]
    assert all(a["readings"] == {} for a in result["answers"])
    assert result["answers"][-1]["citations"] == []
    assert result["provenance"][0]["metadata"] == export["entries"][0]["metadata"]
    assert result["status"] == "requires_capture_metadata_and_independent_review"
    assert "model" not in result


@pytest.mark.parametrize(
    "change,error",
    [
        ("truncated", "incomplete_answer_evidence"),
        ("missing", "missing_answer_evidence"),
        ("unknown_time", "uncertain_capture_time"),
        ("reused", "reused_answer_entry"),
        ("user", "recorded_assistant_answer_required"),
        ("duplicate", "duplicate_transcript_entry"),
        ("altered", "transcript_binding_mismatch"),
    ],
)
@pytest.mark.features("V9")
@pytest.mark.parametrize("plan_kind", ["visual", "screen"])
def test_rejects_incomplete_or_rewritten_capture(change, error, plan_kind):
    plan, mapping, export = fixture(plan_kind)
    meta = export["entries"][0]["metadata"]
    if change == "truncated":
        meta["evidence_truncated"] = True
    elif change == "missing":
        meta.pop("evidence_refs")
    elif change == "unknown_time":
        meta["evidence_refs"][0]["capture_time_known"] = False
    elif change == "reused":
        mapping.entries[plan.cases[1].id] = mapping.entries[plan.cases[0].id]
    elif change == "user":
        export["entries"][0]["role"] = "user"
    elif change == "duplicate":
        export["entries"].append(export["entries"][0])
    data = bind(mapping, export)
    if change == "altered":
        data += b" "
    with pytest.raises(ValueError, match=error):
        extract_answers(plan, mapping, data)
