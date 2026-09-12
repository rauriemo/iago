"""Evaluate observed workload bounds; physical and memory-stability review remain separate."""

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

BOUND_NAMES = frozenset(
    {
        "rolling_bytes",
        "pin_bytes",
        "pins",
        "rolling_frames",
        "model_image_workers",
        "integration_pending",
        "confirmation_and_cancellation_pending",
        "index_bytes",
        "index_staging_bytes",
        "journal_bytes",
    }
)


def number(value):
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError("invalid_resource_number")
    return value


def assess(rows, *, warmup=600):
    number(warmup)
    if not 0 <= warmup < 5400 or not 3 <= len(rows) <= 7204:
        raise ValueError("invalid_resource_capture")
    start, end = rows[0], rows[-1]
    if start.get("type") != "start" or start.get("version") != 1:
        raise ValueError("invalid_capture_header")
    failures, missing = set(), set()
    if end.get("type") != "complete_capture":
        missing.add("complete_capture")
    samples = [r for r in rows[1:] if r.get("type") == "sample"]
    if not samples:
        raise ValueError("missing_resource_samples")
    if any(r.get("type") not in {"sample", "complete_capture", "incomplete"} for r in rows[1:]):
        raise ValueError("invalid_capture_record")
    if any(r.get("type") != "sample" for r in rows[1:-1]):
        raise ValueError("invalid_capture_order")
    if end.get("type") == "complete_capture" and end.get("samples") != len(samples):
        raise ValueError("capture_count_mismatch")
    interval = number(start.get("interval"))
    if not 1 <= interval <= 30:
        raise ValueError("invalid_capture_interval")
    elapsed = [number(s.get("elapsed")) for s in samples]
    if any(b <= a for a, b in zip(elapsed, elapsed[1:], strict=False)):
        raise ValueError("unordered_resource_samples")
    if number(start.get("duration")) < 5400 or elapsed[-1] < 5400:
        missing.add("ninety_minute_observation")
    if elapsed[0] > interval + 5 or any(
        b - a > interval + 5 for a, b in zip(elapsed, elapsed[1:], strict=False)
    ):
        missing.add("observation_continuity")
    owner = None
    limits, peaks = {}, {}
    rss = []
    resource_times = set()
    for sample in samples:
        value = sample.get("observation", {})
        if not isinstance(value.get("owner"), str) or not value["owner"]:
            raise ValueError("invalid_resource_owner")
        if owner is None:
            owner = value["owner"]
        if value["owner"] != owner:
            raise ValueError("resource_owner_changed")
        bounds = value.get("bounds", {})
        for name in BOUND_NAMES:
            if name not in bounds:
                missing.add("bound:" + name)
                continue
            used, limit = number(bounds[name].get("used")), number(bounds[name].get("limit"))
            if limit <= 0:
                raise ValueError("invalid_resource_limit")
            if name in limits and limits[name] != limit:
                failures.add("changed_limit:" + name)
            limits[name] = limit
            peaks[name] = max(peaks.get(name, 0), used)
            if used > limit:
                failures.add("exceeded:" + name)
        resource = value.get("resources", {})
        latest = resource.get("latest")
        if resource.get("stale") is not False or not latest or latest.get("status") != "available":
            missing.add("fresh_resource_samples")
            continue
        if number(resource.get("age_seconds")) > 15:
            missing.add("fresh_resource_samples")
        if number(latest.get("unavailable_children")):
            missing.add("child_memory")
        stamp = number(latest.get("at"))
        if sample["elapsed"] >= warmup and stamp not in resource_times:
            rss.append(
                (
                    sample["elapsed"],
                    number(latest.get("backend_rss_bytes")),
                    number(latest.get("children_rss_bytes")),
                )
            )
            resource_times.add(stamp)
    memory = {}
    if len(rss) < 3:
        missing.add("post_warmup_memory_samples")
    for index, name in [(1, "backend"), (2, "children")]:
        values = [r[index] for r in rss]
        if not values:
            continue
        monotonic_growth = values[-1] > values[0] and all(
            b >= a for a, b in zip(values, values[1:], strict=False)
        )
        if monotonic_growth:
            failures.add("monotonic_memory_growth:" + name)
        memory[name] = {
            "count": len(values),
            "first_bytes": values[0],
            "last_bytes": values[-1],
            "peak_bytes": max(values),
            "median_bytes": statistics.median(values),
            "net_change_bytes": values[-1] - values[0],
            "monotonic_growth": monotonic_growth,
        }
    return {
        "status": "fail" if failures else "blocked" if missing else "review_required",
        "failures": sorted(failures),
        "missing": sorted(missing),
        "samples": len(samples),
        "elapsed_seconds": elapsed[-1] - elapsed[0],
        "warmup_seconds": warmup,
        "bounds": {name: {"peak": peaks[name], "limit": limits[name]} for name in sorted(peaks)},
        "memory": memory,
        "acceptance_pass": False,
        "physical_qualification": False,
        "scope": "Sampled bound violations and monotonic RSS growth; observations do not prove between-sample bounds, authentic workload, browser memory, latency or stable memory under oscillating growth. Independent sustained-memory review remains required.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup", type=float, default=600)
    args = parser.parse_args()
    try:
        with args.input.open("rb") as source:
            raw = source.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("resource_capture_limit")
        lines = raw.splitlines()
        if len(lines) > 7204 or any(len(line) > 131072 for line in lines):
            raise ValueError("resource_capture_limit")
        report = assess([json.loads(line) for line in lines], warmup=args.warmup)
        report["input_sha256"] = hashlib.sha256(raw).hexdigest()
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print("Resource evaluation unavailable: " + type(exc).__name__)
        return 2
    print("Resource observations: " + report["status"] + "; full acceptance unproven")
    return 2 if report["status"] in {"fail", "blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
