"""Synthetic activity declarations exercise coverage logic, never physical qualification."""

import hashlib
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from reachy_brain.evals.workload_coverage import WorkloadRun, assess


def run_data():
    events = []

    def add(kind, at, **kwargs):
        id = str(len(events))
        events.append(
            {"id": id, "kind": kind, "at": float(at), "evidence": "synthetic:" + id, **kwargs}
        )

    for index, at in enumerate(range(0, 3601, 300)):
        add(["query_camera", "query_screen", "query_document"][index % 3], at)
    for at in range(600, 3601, 600):
        add("reindex", at)
    for kind in ("edit", "move", "delete", "create"):
        for project in range(3):
            add(kind, 100 + len(events), project=str(project))
    for kind in ("thumb", "interruption"):
        for index in range(20):
            add(kind, 1000 + index)
    for index, at in enumerate(range(600, 5401, 600)):
        for offset, kind in enumerate(["calendar_read", "calendar_draft", "calendar_write"]):
            add(
                kind,
                at - 2 + offset,
                cycle=str(index),
                module="calendar",
                account="synthetic-a",
                transport="stdio" if index % 2 == 0 else "streamable-http",
            )
    add("delayed_call", 999, end=1001.0)
    add("reconciliation", 1001, end=1003.0)
    return {
        "origin": "synthetic",
        "duration": 5400.0,
        "windows": [
            {
                "start": 0.0,
                "end": 3600.0,
                "camera": True,
                "screen": True,
                "waves": True,
                "thumbs": True,
                "supported_document_files": 30,
            }
        ],
        "activities": events,
    }


@pytest.mark.features("D6", "V9", "K1", "E1", "P10")
@pytest.mark.scenario("WORKLOAD-DECLARED-COVERAGE")
def test_exact_declared_schedule_does_not_become_acceptance():
    report = assess(WorkloadRun.model_validate(run_data()))
    assert report["declared_activity_coverage_met"] and not report["failures"]
    assert report["calendar_cycles"] == 9 and report["combined_activity_seconds"] == 3600
    assert report["counts"]["thumb"] == report["counts"]["interruption"] == 20
    assert not report["physical_qualification"] and not report["acceptance_pass"]


@pytest.mark.features("D6", "E1")
@pytest.mark.scenario("WORKLOAD-CALENDAR-COMPLETION-TIMING")
def test_calendar_cadence_uses_declared_completion_when_available():
    data = run_data()
    first_write = next(e for e in data["activities"] if e["kind"] == "calendar_write")
    first_write["end"] = first_write["at"] + 1.0
    report = assess(WorkloadRun.model_validate(data))
    assert "ten_minute_calendar_cadence" in report["failures"]
    assert not report["declared_activity_coverage_met"]


@pytest.mark.features("D6", "E1")
@pytest.mark.scenario("WORKLOAD-CALENDAR-STEP-COMPLETION")
@pytest.mark.parametrize(
    "step,overlap", [("calendar_read", True), ("calendar_draft", True), ("calendar_read", False)]
)
def test_calendar_steps_complete_before_the_next_step_starts(step, overlap):
    data = run_data()
    event = next(e for e in data["activities"] if e["kind"] == step)
    event["end"] = event["at"] + (2.0 if overlap else 1.0)
    report = assess(WorkloadRun.model_validate(data))
    assert ("invalid_calendar_cycle" in report["failures"]) is overlap
    assert report["calendar_cycles"] == (8 if overlap else 9)
    assert report["declared_activity_coverage_met"] is not overlap


@pytest.mark.features("D6", "E1")
@pytest.mark.scenario("WORKLOAD-CALENDAR-ACCOUNT-IDENTITY")
def test_calendar_without_account_identity_cannot_establish_cycle_coverage():
    data = run_data()
    for event in data["activities"]:
        event.pop("module", None)
        event.pop("account", None)
    report = assess(WorkloadRun.model_validate(data))
    assert "invalid_calendar_cycle" in report["failures"]
    assert report["calendar_cycles"] == 0


@pytest.mark.features("D6", "E1")
@pytest.mark.scenario("WORKLOAD-CALENDAR-ACCOUNT-ISOLATION")
@pytest.mark.parametrize(
    "field,value", [("module", "other"), ("account", "other"), ("module", " "), ("account", " ")]
)
def test_calendar_cycle_cannot_combine_different_namespaces(field, value):
    data = run_data()
    event = next(e for e in data["activities"] if e["kind"] == "calendar_draft")
    event[field] = value
    report = assess(WorkloadRun.model_validate(data))
    assert "invalid_calendar_cycle" in report["failures"]
    assert report["calendar_cycles"] == 8
    assert not report["declared_activity_coverage_met"]


@pytest.mark.features("D6", "V9", "K1", "E1", "P10")
@pytest.mark.scenario("WORKLOAD-MISSING-REQUIRED-ACTIVITY")
@pytest.mark.parametrize(
    "kind",
    [
        "query_document",
        "reindex",
        "edit",
        "move",
        "delete",
        "create",
        "thumb",
        "interruption",
        "calendar_read",
        "calendar_draft",
        "calendar_write",
        "delayed_call",
        "reconciliation",
    ],
)
def test_missing_required_activity_fails_coverage(kind):
    data = run_data()
    data["activities"] = [e for e in data["activities"] if e["kind"] != kind]
    report = assess(WorkloadRun.model_validate(data))
    assert not report["declared_activity_coverage_met"] and report["failures"]


@pytest.mark.features("D6", "V9", "K1", "E1")
@pytest.mark.scenario("WORKLOAD-CADENCE-AND-DOUBLE-COUNT")
@pytest.mark.parametrize(
    "change", ["gap", "alternation", "overlap", "transport", "corpus", "project"]
)
def test_counts_cannot_hide_cadence_or_coverage_failure(change):
    data = run_data()
    if change == "gap":
        next(e for e in data["activities"] if e["kind"] == "query_screen")["at"] += 1
    elif change == "alternation":
        next(e for e in data["activities"] if e["kind"] == "query_screen")["kind"] = "query_camera"
    elif change == "overlap":
        data["windows"][0]["end"] = 1800.0
        data["windows"].append(dict(data["windows"][0]))
    elif change == "transport":
        for event in data["activities"]:
            if event.get("cycle"):
                event["transport"] = "stdio"
    elif change == "corpus":
        data["windows"][0]["supported_document_files"] = 29
    else:
        for event in data["activities"]:
            if "project" in event:
                event["project"] = "only-one"
    assert not assess(WorkloadRun.model_validate(data))["declared_activity_coverage_met"]


@pytest.mark.features("D6", "E1")
@pytest.mark.scenario("WORKLOAD-INVALID-TRACE")
def test_duplicate_ids_and_out_of_range_times_are_invalid():
    data = run_data()
    data["activities"].append(dict(data["activities"][0]))
    with pytest.raises(ValidationError, match="duplicate_activity_id"):
        WorkloadRun.model_validate(data)
    data = run_data()
    data["activities"][0]["at"] = 5400.1
    with pytest.raises(ValidationError, match="invalid_activity_interval"):
        WorkloadRun.model_validate(data)


@pytest.mark.features("D6", "V9", "K1", "P10")
@pytest.mark.scenario("WORKLOAD-CONTRADICTORY-COVERAGE")
@pytest.mark.parametrize(
    "field,value",
    [
        ("camera", False),
        ("screen", False),
        ("waves", False),
        ("thumbs", False),
        ("supported_document_files", 29),
    ],
)
def test_conflicting_overlapping_windows_cannot_hide_outages(field, value):
    data = run_data()
    data["windows"].append({**data["windows"][0], "start": 600.0, "end": 1200.0, field: value})
    with pytest.raises(ValidationError, match="contradictory_active_windows"):
        WorkloadRun.model_validate(data)


@pytest.mark.features("D6", "V9")
@pytest.mark.scenario("WORKLOAD-COVERAGE-BOUNDARY-CHANGE")
def test_duplicate_observations_and_touching_state_changes_remain_valid():
    data = run_data()
    data["windows"].append(dict(data["windows"][0]))
    data["windows"].append({**data["windows"][0], "start": 3600.0, "end": 5400.0, "screen": False})
    result = assess(WorkloadRun.model_validate(data))
    assert result["combined_activity_seconds"] == 3600
    assert result["declared_activity_coverage_met"]


@pytest.mark.features("D6", "E1")
@pytest.mark.scenario("WORKLOAD-COVERAGE-COMMAND")
def test_command_binds_input_and_preserves_existing_report(tmp_path):
    source, target = tmp_path / "synthetic.json", tmp_path / "coverage.json"
    source.write_text(json.dumps(run_data()), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "reachy_brain.evals.workload_coverage",
        "--input",
        str(source),
        "--output",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["declared_activity_coverage_met"] and not report["acceptance_pass"]
    original = target.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=10).returncode == 2
    assert target.read_bytes() == original
