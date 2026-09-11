"""Reproducible acceptance entrypoint. Missing/skipped evidence cannot pass a suite."""

import argparse
import importlib.metadata
import io
import json
import os
import platform
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotenv import dotenv_values

from reachy_brain.evals.requirements import acceptance_inventory


class EvidenceRedactor:
    """Private local reports still exclude configured credentials and common auth headers."""

    def __init__(self):
        values = {**dotenv_values(".env"), **os.environ}
        self.secrets = sorted(
            {
                str(value)
                for key, value in values.items()
                if value and re.search(r"key|token|secret|password|authorization|cookie", key, re.I)
            },
            key=len,
            reverse=True,
        )

    def __call__(self, text):
        text = str(text)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)(bearer\s+)[^\s'\"<>,]+", r"\1[REDACTED]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
        if len(text) > 65536:
            text = text[:65536] + "\n[Trace truncated at 65536 characters]"
        return text

    def data(self, value):
        if isinstance(value, str):
            return self(value)
        if isinstance(value, list):
            return [self.data(item) for item in value]
        if isinstance(value, dict):
            return {key: self.data(item) for key, item in value.items()}
        return value


class BoundedOutput(io.StringIO):
    def write(self, value):
        remaining = max(0, 262144 - self.tell())
        super().write(value[:remaining])
        return len(value)


class EvidencePlugin:
    def __init__(self):
        self.results = {}
        self.items = {}
        self.redact = EvidenceRedactor()

    def pytest_collection_modifyitems(self, items):
        self.items = {item.nodeid: item for item in items}

    def pytest_runtest_logreport(self, report):
        if report.when != "call" and not (report.failed or report.skipped):
            return
        item = self.items.get(report.nodeid)
        features = item.get_closest_marker("features") if item else None
        scenario = item.get_closest_marker("scenario") if item else None
        status = "blocked" if report.skipped else ("pass" if report.passed else "fail")
        phase = {
            "phase": report.when,
            "status": status,
            "seconds": report.duration,
            "traceback": self.redact(report.longreprtext)
            if report.failed or report.skipped
            else "",
        }
        existing = self.results.get(report.nodeid)
        phases = (existing["phases"] if existing else []) + [phase]
        status = max((p["status"] for p in phases), key={"pass": 0, "blocked": 1, "fail": 2}.get)
        measurements = dict(getattr(report, "user_properties", []))
        self.results[report.nodeid] = {
            "test": report.nodeid,
            "scenario": scenario.args[0] if scenario else report.nodeid,
            "features": list(features.args) if features else [],
            "status": status,
            "seconds": sum(p["seconds"] for p in phases),
            "sample_count": measurements.get("sample_count", 1),
            "expected": measurements.get(
                "expected", "All behavioral assertions in the named test hold"
            ),
            "observed": "Assertions satisfied"
            if status == "pass"
            else "See retained phase tracebacks",
            "phases": phases,
            "measurements": measurements.get("measurements", {}),
            "fixture_kind": "declared by test; offline is not physical evidence",
        }
        if "observed" in measurements:
            self.results[report.nodeid]["observed"] = measurements["observed"]

    def pytest_collectreport(self, report):
        if report.failed:
            self.results[report.nodeid] = {
                "test": report.nodeid,
                "scenario": "collection",
                "features": [],
                "status": "fail",
                "observed": "Test collection failed",
                "sample_count": 0,
                "phases": [
                    {
                        "phase": "collection",
                        "status": "fail",
                        "traceback": self.redact(report.longreprtext),
                    }
                ],
            }


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    ).stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", choices=["offline", "live-provider", "live-pc", "robot"], required=True
    )
    parser.add_argument("--output", type=Path, default=Path("local-data/evidence"))
    parser.add_argument("--select", help="Optional pytest -k subset; never a full suite pass")
    invocation = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(invocation)
    args.output.mkdir(parents=True, exist_ok=True)
    plugin = EvidencePlugin()
    marker = {
        "offline": "not live_provider and not live_pc and not robot",
        "live-provider": "live_provider",
        "live-pc": "live_pc",
        "robot": "robot",
    }[args.suite]
    command = ["tests", "-q", "-m", marker]
    if args.select:
        command += ["-k", args.select]
    output = BoundedOutput()
    with redirect_stdout(output), redirect_stderr(output):
        result = int(pytest.main(command, plugins=[plugin]))
    records = plugin.redact.data(list(plugin.results.values()))
    status = (
        "pass"
        if result == 0 and records and all(r["status"] == "pass" for r in records)
        else "fail"
    )
    if result == 5 or (
        records
        and all(r["status"] in {"blocked", "pass"} for r in records)
        and any(r["status"] == "blocked" for r in records)
    ):
        status = "blocked"
    if not records:
        records = [
            {
                "scenario": "suite-availability",
                "status": "blocked",
                "features": [],
                "observed": "No checks collected for this suite; implementation/evidence required",
                "sample_count": 0,
            }
        ]
    report = {
        "date": datetime.now(UTC).isoformat(),
        "suite": args.suite,
        "status": status,
        "scope": "selected subset" if args.select else "currently implemented suite",
        "release_validated": False,
        "commit": git("rev-parse", "HEAD"),
        "dirty_tree": git("status", "--short"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "command": subprocess.list2cmdline(["iago-verify", *invocation]),
        "entrypoint_argv": ["iago-verify", *invocation],
        "pytest_argv": command,
        "invocation_note": "Exact entrypoint arguments; outer shell/uv wrapper is not captured.",
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "models": {"brain": "gpt-6-astra", "access": "not established by offline tests"},
        "devices": "Test-specific; offline devices are fake/virtual",
        "results": records,
        "acceptance_inventory": acceptance_inventory(),
        "limitation": "Only listed scenarios evaluated; consult FEATURES.json for remaining coverage.",
    }
    name = args.suite + ("-subset" if args.select else "")
    console_path = args.output / f"{name}-pytest-output.txt"
    console_path.write_text(plugin.redact(output.getvalue()), encoding="utf-8")
    report["console_evidence"] = str(console_path)
    failures = []
    for record in records:
        for phase in record.get("phases", []):
            if phase.get("traceback"):
                failures.append(
                    f"{record['scenario']} [{phase['phase']}: {phase['status']}]\n{phase['traceback']}"
                )
    trace_path = args.output / f"{name}-failures.txt"
    trace_path.write_text(
        "\n\n".join(failures) if failures else "No failing or blocked phases.\n", encoding="utf-8"
    )
    report["failure_evidence"] = str(trace_path)
    json_path = args.output / f"{name}.json"
    report = plugin.redact.data(report)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"# Iago {args.suite} verification",
        "",
        f"Status: **{status}**. {report['limitation']}",
        f"Commit: {report['commit']}. Full release validated: no.",
        "",
        f"Command: `{report['command']}`",
        f"Failure/blocked details: [{trace_path.name}]({trace_path.name}) (redacted, bounded per phase).",
        "",
        "| Scenario | Features | Status |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {r['scenario']} | {', '.join(r['features'])} | {r['status']} |" for r in records]
    inventory = report["acceptance_inventory"]
    lines += [
        "",
        "## Mandatory acceptance coverage",
        "",
        inventory["notice"],
        "",
        f"Inventory status: **{inventory['status']}**. Full texts and source hashes are in the JSON report.",
        "",
        "| Requirement | Source line | Coverage |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {r['id']} | {r['source']}:{r['line']} | {r['coverage_status']} |"
        for r in inventory["requirements"]
    ]
    (args.output / f"{name}.md").write_text(
        plugin.redact("\n".join(lines)) + "\n", encoding="utf-8"
    )
    print(f"Evidence: {json_path}; status={status}")
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
