"""Synthetic bound workload artifacts; never a physical or complete soak pass."""

import hashlib
import json
import subprocess
import sys

import pytest
from test_workload_coverage import run_data
from test_workload_resources import trace

from reachy_brain.evals.workload_bundle import WorkloadBundle, score_bundle


def bundle_files(root, fault="none"):
    rows, activities = trace(), run_data()
    timings = {}
    for family, operation in [
        ("project", "refresh"),
        ("tools", "read"),
        ("capture", "camera_archive"),
        ("perception", "process_frame"),
        ("retrieval", "combined_retrieval"),
        ("gesture", "thumb_up_commit"),
    ]:
        sample = {"sequence": 1, "operation": operation, "outcome": "ok", "seconds": 0.1}
        if family == "retrieval":
            sample["limit_seconds"] = 2.0
        if family == "gesture":
            sample["source_uncertainty_seconds"] = 0.01
        timings[family] = {"total": 1, "samples": [sample]}
    timings["response"] = {
        "available": True,
        "owner": "synthetic-response",
        "total": 1,
        "samples": [
            {"sequence": 1, "origin": "typed", "status": "finished", "stages": {"ended": 1.0}}
        ],
    }
    if fault == "missing_timing":
        del timings["gesture"]
    for row in rows[1:-1]:
        row["observation"]["timings"] = timings
    if fault == "incomplete":
        rows[-1]["type"] = "incomplete"
    if fault == "duration":
        rows[0]["duration"] = 5401
    if fault == "bounds":
        rows[50]["observation"]["bounds"]["journal_bytes"]["used"] = 101
    if fault == "coverage":
        activities["activities"] = [e for e in activities["activities"] if e["kind"] != "thumb"]
    artifacts = {}
    for name, data in [
        ("capture", "".join(json.dumps(row) + "\n" for row in rows).encode()),
        ("activities", json.dumps(activities).encode()),
    ]:
        file = name + ".json"
        (root / file).write_bytes(data)
        artifacts[name] = {"file": file, "sha256": hashlib.sha256(data).hexdigest()}
    if fault == "changed":
        (root / "activities.json").write_bytes(b"{}")
    return WorkloadBundle.model_validate(artifacts)


@pytest.mark.features("D6", "E1", "K1", "V9", "P10")
@pytest.mark.scenario("WORKLOAD-BOUND-COMPONENT-REPORT")
@pytest.mark.parametrize(
    "fault", ["none", "missing_timing", "incomplete", "duration", "bounds", "coverage", "changed"]
)
def test_bound_reports_preserve_failures_and_review_requirements(tmp_path, fault):
    bundle = bundle_files(tmp_path, fault)
    if fault in {"duration", "changed"}:
        with pytest.raises(ValueError):
            score_bundle(tmp_path, bundle)
        return
    result = score_bundle(tmp_path, bundle)
    expected = (
        "review_required"
        if fault == "none"
        else "fail"
        if fault in {"bounds", "coverage"}
        else "blocked"
    )
    assert result["status"] == expected
    assert not result["acceptance_pass"] and not result["physical_qualification"]
    assert result["artifacts"] == bundle.model_dump()
    if fault == "missing_timing":
        assert result["missing_timing_families"] == ["gesture"]


@pytest.mark.features("D6")
@pytest.mark.scenario("WORKLOAD-BUNDLE-COMMAND")
def test_command_retains_report_and_refuses_overwrite(tmp_path):
    bundle = bundle_files(tmp_path)
    manifest, output = tmp_path / "manifest.json", tmp_path / "report.json"
    manifest.write_text(bundle.model_dump_json())
    command = [
        sys.executable,
        "-m",
        "reachy_brain.evals.workload_bundle",
        "--manifest",
        str(manifest),
        "--output",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    original = output.read_bytes()
    assert json.loads(original)["status"] == "review_required"
    assert (
        json.loads(original)["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert subprocess.run(command, capture_output=True, timeout=20).returncode != 0
    assert output.read_bytes() == original
    manifest.write_text('{"private": "SYNTHETIC_PRIVATE_MANIFEST"}')
    failed = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert failed.returncode == 2
    assert "SYNTHETIC_PRIVATE_MANIFEST" not in failed.stdout + failed.stderr
    assert output.read_bytes() == original
