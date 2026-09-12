"""Backend timing evidence; synthetic operations do not qualify physical latency."""

import copy
import hashlib
import json
import subprocess
import sys

import pytest

from reachy_brain.evals.workload_timings import summarize
from reachy_brain.integrations.registry import CallContext, Rule
from tests.test_integrations import system as system


def observation():
    return {
        "owner": "application",
        "timings": {
            "project": {
                "total": 2,
                "samples": [
                    {"sequence": i, "operation": "search", "outcome": "ok", "seconds": i}
                    for i in (1, 2)
                ],
            },
            "tools": {
                "total": 1,
                "samples": [{"sequence": 1, "operation": "write", "outcome": "ok", "seconds": 3}],
            },
            "response": {
                "available": True,
                "owner": "conversation",
                "total": 1,
                "samples": [
                    {
                        "sequence": 1,
                        "origin": "typed",
                        "status": "running",
                        "stages": {"model_start": 0.1},
                    }
                ],
            },
        },
    }


@pytest.mark.features("D6", "K1", "E1")
@pytest.mark.scenario("WORKLOAD-TIMING-DEDUP-DISTRIBUTIONS")
def test_repeated_snapshots_and_running_response_updates():
    before = observation()
    after = copy.deepcopy(before)
    after["timings"]["response"]["samples"][0].update(
        status="finished", stages={"model_start": 0.1, "first_audio_dispatch": 2, "ended": 3}
    )
    result = summarize([before, after, after])
    assert result["status"] == "partial_report" and result["missing_sequence_count"] == 0
    project = next(r for r in result["distributions"] if r["family"] == "project")
    assert project["count"] == 2 and project["median_seconds"] == 1.5
    assert project["p95_seconds"] == project["slowest_seconds"] == 2
    assert not result["acceptance_pass"] and not result["physical_qualification"]


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-TIMING-MISSING")
def test_dropped_sequences_and_unfinished_response_block():
    value = observation()
    value["timings"]["project"]["samples"].pop(0)
    result = summarize([value])
    assert result["status"] == "blocked"
    assert result["missing_sequence_count"] == 1 and result["running_responses"] == 1


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-TIMING-INVALID")
@pytest.mark.parametrize("fault", ["changed", "reversed", "owner", "nan", "category"])
def test_timing_corruption_rejected(fault):
    before = observation()
    after = copy.deepcopy(before)
    stream = after["timings"]["project"]
    if fault == "changed":
        stream["samples"][0]["seconds"] = 100
    if fault == "reversed":
        stream["total"] = 1
    if fault == "owner":
        after["owner"] = "replacement"
    if fault == "nan":
        before["timings"]["tools"]["samples"][0]["seconds"] = float("nan")
        after = copy.deepcopy(before)
    if fault == "category":
        stream["samples"][0]["operation"] = "private-content"
    with pytest.raises(ValueError):
        summarize([before, after])


@pytest.mark.features("D6", "E1")
@pytest.mark.scenario("TOOL-TIMING-PRIVATE-BOUNDED")
async def test_real_executor_records_bounded_private_timing(system):
    registry, policy, journal, executor, changes = system
    key = "calendar__a__create"
    policy.set(Rule(key, "write", "allow"))
    result = await executor.execute(
        key,
        {"title": "private-title"},
        CallContext("private-session", 1, operation_id="measured-write"),
    )
    assert result["status"] == "ok"
    assert executor.timing_snapshot()["samples"][0]["operation"] == "write"
    for i in range(70):
        await executor.execute(
            "missing-private-tool", {}, CallContext("private-session", 1, operation_id=str(i))
        )
    snapshot = executor.timing_snapshot()
    assert snapshot["total"] == 71 and len(snapshot["samples"]) == 64
    assert all(r["seconds"] >= 0 for r in snapshot["samples"])
    assert "private" not in json.dumps(snapshot) and "calendar" not in json.dumps(snapshot)
    await executor.close()


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-TIMING-COMMAND")
def test_timing_command_hash_and_exclusive_output(tmp_path):
    value = observation()
    value["timings"]["response"]["samples"][0]["status"] = "finished"
    rows = [
        {"type": "start"},
        {"type": "sample", "observation": value},
        {"type": "complete_capture"},
    ]
    source = tmp_path / "observed.jsonl"
    source.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    output = tmp_path / "timings.json"
    command = [
        sys.executable,
        "-m",
        "reachy_brain.evals.workload_timings",
        "--input",
        str(source),
        "--output",
        str(output),
    ]
    done = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert done.returncode == 0, done.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    before = output.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=15).returncode == 2
    assert output.read_bytes() == before


@pytest.mark.features("D6", "V3", "K1")
@pytest.mark.scenario("COMBINED-RETRIEVAL-DEADLINE-REPORT")
@pytest.mark.parametrize(
    "outcome,seconds,status",
    [("ok", 9, "partial_report"), ("ok", 11, "fail"), ("error", 12, "partial_report")],
)
def test_successful_retrieval_must_fit_its_declared_deadline(outcome, seconds, status):
    value = {
        "owner": "application",
        "timings": {
            "retrieval": {
                "owner": "conversation",
                "total": 1,
                "samples": [
                    {
                        "sequence": 1,
                        "operation": "combined_retrieval",
                        "outcome": outcome,
                        "seconds": seconds,
                        "limit_seconds": 10,
                    }
                ],
            }
        },
    }
    result = summarize([value, value])
    assert result["status"] == status
    assert result["retrieval_limits_seconds"] == [10]
    assert result["successful_retrieval_deadline_overruns"] == (1 if status == "fail" else 0)
    assert not result["acceptance_pass"]
