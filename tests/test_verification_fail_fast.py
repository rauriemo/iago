"""Actual child verification runs; failure/early exit must never hide unexecuted checks."""

import json
import os
import subprocess
import sys

import pytest


def run(tmp_path, source, *extra):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ordered.py").write_text(source, encoding="utf-8")
    output = tmp_path / "evidence"
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "reachy_brain.verification",
            "--suite",
            "offline",
            "--output",
            str(output),
            *extra,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    report = json.loads(
        (output / ("offline-subset.json" if "--select" in extra else "offline.json")).read_text(
            encoding="utf-8"
        )
    )
    return result, report


@pytest.mark.features("D5", "D6")
@pytest.mark.scenario("EVIDENCE-FAIL-FAST-EXECUTION")
@pytest.mark.parametrize("fast", [False, True])
def test_explicit_fail_fast_stops_later_work_and_retains_failure(tmp_path, fast):
    result, report = run(
        tmp_path,
        """
from pathlib import Path
def test_00_failure():
    raise RuntimeError("synthetic provider failure")
def test_01_later():
    Path("later-ran.txt").write_text("synthetic later work")
""",
        *(["--fail-fast"] if fast else []),
    )
    assert result.returncode == 2 and report["status"] == "fail"
    assert not report["release_validated"]
    assert (tmp_path / "later-ran.txt").exists() is not fast
    assert report["selected_test_count"] == 2
    assert len(report["not_run_tests"]) == int(fast)
    assert ("-x" in report["pytest_argv"]) is fast
    if fast:
        assert report["not_run_tests"][0].endswith("test_01_later")
        assert "--fail-fast" in report["entrypoint_argv"]


@pytest.mark.features("D5", "D6")
@pytest.mark.scenario("EVIDENCE-DESELECTED-NOT-MISSING")
def test_selected_subset_excludes_deselected_items_from_not_run(tmp_path):
    result, report = run(
        tmp_path,
        """
def test_chosen(): pass
def test_unselected(): raise AssertionError("must remain deselected")
""",
        "--select",
        "test_chosen",
        "--fail-fast",
    )
    assert result.returncode == 0 and report["status"] == "pass"
    assert report["selected_test_count"] == 1 and report["not_run_tests"] == []
    assert report["scope"] == "selected subset"


@pytest.mark.features("D5", "D6")
@pytest.mark.scenario("EVIDENCE-EARLY-ZERO-EXIT-NOT-PASS")
def test_pytest_early_zero_exit_cannot_pass_unexecuted_checks(tmp_path):
    result, report = run(
        tmp_path,
        """
import pytest
def test_00_pass(): pass
def test_01_exit(): pytest.exit("synthetic early exit", returncode=0)
def test_02_missing(): pass
""",
    )
    assert result.returncode == 2 and report["status"] == "blocked"
    assert report["selected_test_count"] == 3 and len(report["not_run_tests"]) == 2
    assert len(report["results"]) == 1 and report["results"][0]["status"] == "pass"
