"""Synthetic resource traces exercise rejection, not physical soak qualification."""

import hashlib
import json
import subprocess
import sys

import pytest

from reachy_brain.evals.workload_resources import BOUND_NAMES, assess


def trace():
    rows = [{"type": "start", "version": 1, "duration": 5400, "interval": 30}]
    for i in range(181):
        rows.append(
            {
                "type": "sample",
                "elapsed": i * 30,
                "observation": {
                    "owner": "synthetic-run",
                    "bounds": {name: {"used": 1, "limit": 100} for name in BOUND_NAMES},
                    "resources": {
                        "stale": False,
                        "age_seconds": 0,
                        "latest": {
                            "at": 1000 + i * 30,
                            "status": "available",
                            "unavailable_children": 0,
                            "backend_rss_bytes": 1000 + (i % 2) * 100,
                            "children_rss_bytes": 100,
                        },
                    },
                },
            }
        )
    rows.append({"type": "complete_capture", "samples": 181, "elapsed": 5400})
    return rows


@pytest.mark.features("D6", "V9", "K1", "E1")
@pytest.mark.scenario("WORKLOAD-RESOURCE-OBSERVATIONS")
def test_resource_report_requires_review_even_without_observed_violations():
    result = assess(trace())
    assert result["status"] == "review_required"
    assert not result["failures"] and not result["missing"]
    assert not result["acceptance_pass"] and not result["physical_qualification"]
    assert result["memory"]["backend"]["peak_bytes"] == 1100
    assert result["memory"]["backend"]["count"] == 161
    assert set(result["bounds"]) == BOUND_NAMES


@pytest.mark.features("D6", "V9", "K1", "E1")
@pytest.mark.scenario("WORKLOAD-RESOURCE-BOUND-VIOLATION")
@pytest.mark.parametrize("name", sorted(BOUND_NAMES))
def test_each_observed_bound_is_enforced(name):
    rows = trace()
    rows[50]["observation"]["bounds"][name]["used"] = 101
    result = assess(rows)
    assert result["status"] == "fail" and "exceeded:" + name in result["failures"]


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-RESOURCE-INCOMPLETE")
@pytest.mark.parametrize(
    "kind", ["short", "stale", "missing_bound", "incomplete", "gap", "children"]
)
def test_missing_observations_never_pass(kind):
    rows = trace()
    if kind == "short":
        rows[0]["duration"] = 100
    if kind == "stale":
        rows[5]["observation"]["resources"]["stale"] = True
    if kind == "missing_bound":
        rows[5]["observation"]["bounds"].pop("journal_bytes")
    if kind == "incomplete":
        rows[-1]["type"] = "incomplete"
    if kind == "gap":
        rows.pop(5)
        rows[-1]["samples"] -= 1
    if kind == "children":
        rows[5]["observation"]["resources"]["latest"]["unavailable_children"] = 1
    result = assess(rows)
    assert result["status"] == "blocked" and result["missing"]


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-MONOTONIC-MEMORY")
def test_increasing_memory_and_changed_limits_fail():
    rows = trace()
    for i, row in enumerate(rows[1:-1]):
        row["observation"]["resources"]["latest"]["backend_rss_bytes"] = 1000 + i
    rows[50]["observation"]["bounds"]["rolling_bytes"]["limit"] = 200
    result = assess(rows)
    assert result["status"] == "fail"
    assert "monotonic_memory_growth:backend" in result["failures"]
    assert "changed_limit:rolling_bytes" in result["failures"]


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-RESOURCE-INVALID")
@pytest.mark.parametrize("kind", ["nan", "negative", "owner", "order", "count"])
def test_invalid_resource_trace_rejected(kind):
    rows = trace()
    if kind == "nan":
        rows[5]["elapsed"] = float("nan")
    if kind == "negative":
        rows[5]["observation"]["bounds"]["pins"]["used"] = -1
    if kind == "owner":
        rows[5]["observation"]["owner"] = "replacement"
    if kind == "order":
        rows[5]["elapsed"] = 0
    if kind == "count":
        rows[-1]["samples"] = 100
    with pytest.raises(ValueError):
        assess(rows)


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-RESOURCE-COMMAND")
def test_command_binds_input_and_preserves_existing_report(tmp_path):
    source = tmp_path / "capture.jsonl"
    source.write_text("\n".join(json.dumps(r) for r in trace()) + "\n", encoding="utf-8")
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "reachy_brain.evals.workload_resources",
        "--input",
        str(source),
        "--output",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    before = output.read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=15).returncode == 2
    assert output.read_bytes() == before
