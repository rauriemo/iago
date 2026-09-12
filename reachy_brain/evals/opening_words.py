"""Compare frozen interruption openings to saved recognition; no physical-pass inference."""

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from reachy_brain.evals.screen_grounding import StrictRecord, digest


class OpeningCase(StrictRecord):
    id: str = Field(min_length=1, max_length=128)
    provider: Literal["openai", "elevenlabs", "fallback"]
    expected_opening: str = Field(min_length=1, max_length=500)


class OpeningPlan(StrictRecord):
    version: Literal[1]
    cases: list[OpeningCase] = Field(min_length=20, max_length=1000)


class OpeningMapping(StrictRecord):
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: dict[str, str] = Field(min_length=20, max_length=1000)


class OpeningReview(StrictRecord):
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acoustic_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    independent_human_review: bool
    # Case ID -> spoken CutoffTrial ID; all declared spoken trials must be covered.
    recording_trials: dict[str, str] = Field(min_length=20, max_length=1000)
    utterance_and_overlap_verified: dict[str, bool] = Field(min_length=20, max_length=1000)
    labels_frozen_before_run: bool


def validate_opening_review(plan, mapping, review, acoustic_bytes, trials):
    if (
        review.mapping_sha256 != digest(mapping)
        or review.acoustic_manifest_sha256 != hashlib.sha256(acoustic_bytes).hexdigest()
    ):
        raise ValueError("review_binding_mismatch")
    if (
        not review.independent_human_review
        or not review.reviewer.strip()
        or not review.labels_frozen_before_run
    ):
        raise ValueError("independent_frozen_review_required")
    cases = {case.id: case for case in plan.cases}
    if set(review.recording_trials) != set(cases) or set(
        review.utterance_and_overlap_verified
    ) != set(cases):
        raise ValueError("review_coverage_mismatch")
    spoken = {t.id: t for t in trials if t.action == "spoken"}
    assigned = list(review.recording_trials.values())
    if (
        len(spoken) != sum(t.action == "spoken" for t in trials)
        or len(set(assigned)) != len(assigned)
        or set(assigned) != set(spoken)
    ):
        raise ValueError("recorded_interruption_coverage_mismatch")
    for case_id, trial_id in review.recording_trials.items():
        if spoken[trial_id].provider != cases[case_id].provider:
            raise ValueError("recorded_provider_mismatch")
        if not review.utterance_and_overlap_verified[case_id]:
            raise ValueError("utterance_or_overlap_unverified")
    return [spoken[review.recording_trials[case.id]] for case in plan.cases]


def words(text):
    return re.findall(r"\w+(?:'\w+)*", text.replace("’", "'").casefold())


def score_openings(plan: OpeningPlan, mapping: OpeningMapping, exported: bytes):
    if len(exported) > 32 * 1024 * 1024:
        raise ValueError("transcript_size_limit")
    if (
        mapping.plan_sha256 != digest(plan)
        or mapping.transcript_sha256 != hashlib.sha256(exported).hexdigest()
    ):
        raise ValueError("evidence_binding_mismatch")
    ids = [case.id for case in plan.cases]
    if len(set(ids)) != len(ids) or set(ids) != set(mapping.entries):
        raise ValueError("case_coverage_mismatch")
    if len(set(mapping.entries.values())) != len(ids):
        raise ValueError("reused_recognition_entry")
    if {case.provider for case in plan.cases} != {"openai", "elevenlabs", "fallback"}:
        raise ValueError("provider_coverage_missing")
    export = json.loads(exported)
    if not isinstance(export, dict) or export.get("format_version") != 1:
        raise ValueError("unsupported_transcript_export")
    rows = export.get("entries")
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("invalid_transcript_entries")
    index = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("entry"), str):
            raise ValueError("invalid_transcript_entry")
        if row["entry"] in index:
            raise ValueError("duplicate_transcript_entry")
        index[row["entry"]] = row
    results, recognition_ids = [], set()
    for case in plan.cases:
        expected = words(case.expected_opening)
        if not expected:
            raise ValueError("opening_words_required")
        row = index.get(mapping.entries[case.id])
        # A missing entry is a measured missing recognition, not a silently skipped case.
        if row is None:
            results.append(
                {
                    "id": case.id,
                    "provider": case.provider,
                    "preserved": False,
                    "status": "missing_recognition",
                    "expected_words": len(expected),
                }
            )
            continue
        if row.get("role") != "user" or row.get("kind") != "speech":
            raise ValueError("speech_recognition_entry_required")
        text, metadata = row.get("text"), row.get("metadata")
        if not isinstance(text, str) or len(text) > 12000 or not isinstance(metadata, dict):
            raise ValueError("invalid_recognition_entry")
        identity = metadata.get("recognition_id")
        if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
            raise ValueError("recognition_identity_required")
        if identity in recognition_ids:
            raise ValueError("reused_recognition_identity")
        recognition_ids.add(identity)
        actual = words(text)
        preserved = actual[: len(expected)] == expected
        results.append(
            {
                "id": case.id,
                "provider": case.provider,
                "preserved": preserved,
                "status": "preserved" if preserved else "opening_mismatch",
                "expected_words": len(expected),
            }
        )
    return {
        "plan_sha256": digest(plan),
        "transcript_sha256": mapping.transcript_sha256,
        "trials": results,
        "preserved": sum(r["preserved"] for r in results),
        "total": len(results),
        "all_openings_preserved": all(r["preserved"] for r in results),
        "physical_capture_verified": False,
        "limitation": "Compares recorded text prefixes only. Independent recordings/review must establish uttered words, overlap, provider and frozen labels. Does not measure cutoff or zero self-triggered turns.",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "mapping", "transcript", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    data = []
    for path, cap in (
        (args.plan, 1024 * 1024),
        (args.mapping, 1024 * 1024),
        (args.transcript, 32 * 1024 * 1024),
    ):
        with path.open("rb") as stream:
            raw = stream.read(cap + 1)
        if len(raw) > cap:
            raise ValueError("opening_input_size_limit")
        data.append(raw)
    result = score_openings(
        OpeningPlan.model_validate_json(data[0]),
        OpeningMapping.model_validate_json(data[1]),
        data[2],
    )
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    return 0 if result["all_openings_preserved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
