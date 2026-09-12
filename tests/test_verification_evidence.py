"""Child pytest runs deliberately fail; their evidence must stay failed and redact fake secrets."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.features("D5", "D6")
@pytest.mark.scenario("EVIDENCE-FAILURE-PHASES-REDACTION")
def test_failure_and_teardown_details_survive_without_credentials(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_deliberate.py").write_text(
        """
import os
import pytest
@pytest.fixture
def resource():
    yield
    raise RuntimeError("cleanup_failure " + os.environ["IAGO_TEST_SECRET"])
def test_failure(resource):
    raise ValueError("call_failure " + os.environ["IAGO_TEST_SECRET"])
def test_blocked():
    pytest.skip("fixture_unavailable")
""",
        encoding="utf-8",
    )
    output = tmp_path / "evidence"
    secret = "synthetic-private-credential-78394"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reachy_brain.verification",
            "--suite",
            "offline",
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env={**os.environ, "IAGO_TEST_SECRET": secret},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert secret not in result.stdout + result.stderr
    report = json.loads((output / "offline.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail" and not report["release_validated"]
    assert report["acceptance_inventory"]["status"] == "unavailable"
    assert "Mandatory acceptance coverage" in (output / "offline.md").read_text(encoding="utf-8")
    failure = next(r for r in report["results"] if r["test"].endswith("test_failure"))
    assert failure["status"] == "fail"
    assert [p["phase"] for p in failure["phases"]] == ["call", "teardown"]
    assert "call_failure" in failure["phases"][0]["traceback"]
    assert "cleanup_failure" in failure["phases"][1]["traceback"]
    assert any(r["status"] == "blocked" for r in report["results"])
    for artifact in output.iterdir():
        assert secret not in artifact.read_text(encoding="utf-8")
    assert "[REDACTED]" in (output / "offline-failures.txt").read_text(encoding="utf-8")
    assert report["entrypoint_argv"][-1] == str(output)


@pytest.mark.features("D5", "D6")
@pytest.mark.scenario("EVIDENCE-COLLECTION-FAILURE")
def test_collection_failure_retains_traceback(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_broken.py").write_text(
        'raise RuntimeError("deliberate_collection_error")\n', encoding="utf-8"
    )
    output = tmp_path / "evidence"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reachy_brain.verification",
            "--suite",
            "offline",
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    report = json.loads((output / "offline.json").read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert "deliberate_collection_error" in report["results"][0]["phases"][0]["traceback"]


@pytest.mark.features("D5", "D6", "E1")
@pytest.mark.scenario("EVIDENCE-STRUCTURED-CREDENTIAL-REDACTION")
def test_structured_measurements_redact_keys_and_tuples_without_losing_rows(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_structured.py").write_text(
        """
import os
def test_measurement(record_property):
    secret = os.environ["IAGO_TEST_SECRET"]
    record_property("measurements", {
        "rows": {secret: "first", "[REDACTED]": "second", "[REDACTED] [2]": "third"},
        "nested": ({"value": secret}, ("prefix " + secret,)),
        "sample_counts": (2, 3),
    })
""",
        encoding="utf-8",
    )
    output = tmp_path / "evidence"
    secret = "synthetic-structured-credential-54671"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reachy_brain.verification",
            "--suite",
            "offline",
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env={**os.environ, "IAGO_TEST_SECRET": secret},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert secret not in result.stdout + result.stderr
    for artifact in output.iterdir():
        assert secret not in artifact.read_text(encoding="utf-8")
    report = json.loads((output / "offline.json").read_text(encoding="utf-8"))
    measured = report["results"][0]["measurements"]
    assert sorted(measured["rows"].values()) == ["first", "second", "third"]
    assert measured["nested"] == [{"value": "[REDACTED]"}, ["prefix [REDACTED]"]]
    assert measured["sample_counts"] == [2, 3]
