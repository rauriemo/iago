"""Bind workload declarations and observations; independent physical review remains required."""

import hashlib
import json
from pathlib import Path

from .robot_motion_bundle import Artifact, read_bound
from .screen_grounding import StrictRecord
from .workload_coverage import WorkloadRun
from .workload_coverage import assess as assess_coverage
from .workload_resources import assess as assess_resources
from .workload_timings import summarize


class WorkloadBundle(StrictRecord):
    capture: Artifact
    activities: Artifact


def score_bundle(root: Path, bundle: WorkloadBundle):
    raw = read_bound(root, bundle.capture, limit=32 * 1024 * 1024)
    lines = raw.splitlines()
    if len(lines) > 7204 or any(len(line) > 131072 for line in lines):
        raise ValueError("workload_capture_limit")
    rows = [json.loads(line) for line in lines]
    resources = assess_resources(rows, warmup=600)
    samples = [row for row in rows if row.get("type") == "sample"]
    run = WorkloadRun.model_validate_json(
        read_bound(root, bundle.activities, limit=4 * 1024 * 1024)
    )
    if run.duration != rows[0].get("duration"):
        raise ValueError("workload_duration_binding_mismatch")
    coverage = assess_coverage(run)
    timings = summarize([sample["observation"] for sample in samples])
    required = {"project", "tools", "response", "capture", "perception", "retrieval", "gesture"}
    missing = sorted(required - {row["family"] for row in timings["distributions"]})
    status = (
        "fail"
        if not coverage["declared_activity_coverage_met"]
        or resources["status"] == "fail"
        or timings["status"] == "fail"
        else "blocked"
        if resources["status"] == "blocked" or timings["status"] == "blocked" or missing
        else "review_required"
    )
    return {
        "status": status,
        "artifacts": bundle.model_dump(),
        "coverage": coverage,
        "resources": resources,
        "timings": timings,
        "missing_timing_families": missing,
        "acceptance_pass": False,
        "physical_qualification": False,
        "scope": "Exact artifact binding and component checks only. Declarations do not authenticate operation identity, physical continuity, browser memory, acoustic timing or independent human review. A complete family list does not establish every required timing scenario.",
    }


def _main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.manifest.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("workload_manifest_limit")
    bundle = WorkloadBundle.model_validate_json(raw)
    result = score_bundle(args.manifest.parent, bundle)
    result["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print("Workload bundle: " + result["status"] + "; physical qualification remains unproven.")
    return 0 if result["status"] == "review_required" else 2


def main():
    try:
        return _main()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print("Workload bundle unavailable: " + type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
