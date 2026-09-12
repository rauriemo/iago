"""Summarize backend workload timings; physical latency gates are separate."""

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def summarize(observations):
    samples, totals, owners = {}, {}, set()
    for observation in observations:
        owners.add(observation["owner"])
        if len(owners) != 1:
            raise ValueError("workload_owner_changed")
        for family in (
            "project",
            "tools",
            "response",
            "capture",
            "perception",
            "retrieval",
            "gesture",
        ):
            stream = observation.get("timings", {}).get(family)
            if stream is None or (family == "response" and not stream.get("available")):
                continue
            owner = stream.get("owner", observation["owner"])
            if not isinstance(owner, str) or not owner:
                raise ValueError("invalid_timing_owner")
            total = stream.get("total")
            if type(total) is not int or not 0 <= total <= 1000000:
                raise ValueError("invalid_timing_total")
            key = (family, owner)
            if total < totals.get(key, 0):
                raise ValueError("timing_counter_reversed")
            totals[key] = total
            for row in stream["samples"]:
                seq = row.get("sequence")
                if type(seq) is not int or not 1 <= seq <= total:
                    raise ValueError("invalid_timing_sequence")
                identity = (*key, seq)
                before = samples.get(identity)
                if before and before != row:
                    if family != "response" or before["status"] != "running":
                        raise ValueError("changed_completed_timing")
                    if any(row["stages"].get(k) != v for k, v in before["stages"].items()):
                        raise ValueError("response_stage_changed")
                samples[identity] = row
    groups = defaultdict(list)
    running = 0
    retrieval_overruns = 0
    retrieval_limits = set()
    gesture_uncertainties = []
    for (family, _owner, _sequence), row in samples.items():
        if family == "response":
            if row["status"] == "running":
                running += 1
                continue
            if row["origin"] not in {"typed", "speech", "gesture", "initiative", "other"} or row[
                "status"
            ] not in {"finished", "canceled", "error", "speech_failed"}:
                raise ValueError("invalid_response_timing_category")
            for stage, seconds in row["stages"].items():
                if stage not in {
                    "model_start",
                    "first_model_text",
                    "first_audio_dispatch",
                    "retrieval_requested",
                    "other_tool_requested",
                    "voice_fallback",
                    "speech_failed",
                    "ended",
                }:
                    raise ValueError("invalid_response_stage")
                groups[(family, row["origin"], row["status"], stage)].append(seconds)
        else:
            allowed = {
                "project": {"search", "read", "refresh"},
                "tools": {"read", "draft", "write", "unknown"},
                "capture": {
                    "camera_archive",
                    "screen_archive",
                    "upload_archive",
                    "unknown_archive",
                },
                "perception": {"process_frame"},
                "retrieval": {"combined_retrieval"},
                "gesture": {
                    f"{gesture}_{stage}"
                    for gesture in ("thumb_up", "thumb_down")
                    for stage in ("recognition_observed", "arbitration", "commit")
                },
            }[family]
            outcomes = (
                {"ok", "no_op", "error"}
                if family == "project"
                else {"ok", "unsuccessful", "canceled", "error"}
            )
            if row["operation"] not in allowed or row["outcome"] not in outcomes:
                raise ValueError("invalid_operation_timing_category")
            if family == "gesture":
                uncertainty = row.get("source_uncertainty_seconds")
                if (
                    type(uncertainty) not in {int, float}
                    or not math.isfinite(uncertainty)
                    or uncertainty < 0
                ):
                    raise ValueError("missing_or_invalid_gesture_clock_uncertainty")
                gesture_uncertainties.append(uncertainty)
            if family == "retrieval":
                limit = row.get("limit_seconds")
                if type(limit) not in {int, float} or not math.isfinite(limit) or limit <= 0:
                    raise ValueError("invalid_retrieval_limit")
                retrieval_limits.add(limit)
                if row["outcome"] == "ok" and row["seconds"] > limit:
                    retrieval_overruns += 1
            groups[(family, row["operation"], row["outcome"], "completion")].append(row["seconds"])
    distributions = []
    for category, values in sorted(groups.items()):
        if any(type(v) not in {float, int} or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("invalid_timing_duration")
        values.sort()
        distributions.append(
            dict(zip(("family", "operation", "outcome", "stage"), category, strict=True))
            | {
                "count": len(values),
                "median_seconds": statistics.median(values),
                "p95_seconds": values[math.ceil(0.95 * len(values)) - 1],
                "slowest_seconds": values[-1],
            }
        )
    lost = sum(total - sum(k[:2] == stream for k in samples) for stream, total in totals.items())
    return {
        "distributions": distributions,
        "missing_sequence_count": lost,
        "running_responses": running,
        "retrieval_limits_seconds": sorted(retrieval_limits),
        "successful_retrieval_deadline_overruns": retrieval_overruns,
        "max_gesture_source_uncertainty_seconds": max(gesture_uncertainties, default=None),
        "status": "fail"
        if retrieval_overruns
        else "blocked"
        if lost
        or running
        or not distributions
        or any(value > 0.1 for value in gesture_uncertainties)
        else "partial_report",
        "acceptance_pass": False,
        "physical_qualification": False,
        "scope": "Deduplicated backend timings only. Document execution excludes its external worker queue; tool execution includes its queue/journal but excludes proposal, confirmation and reconciliation. Response dispatch is not audible latency. Capture covers backend HTTP upload/archive; perception covers returned worker execution only. Combined retrieval covers elapsed budget time through the last retrieval decision and checks successful completions against their configured deadline. Gesture timings cover initial observed recognition, arbitration and backend commit, with reported source-clock uncertainty; later speech may supersede them. Physical capture/gesture/audio gates still require separate measurements.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.input.open("rb") as source:
            raw = source.read(32 * 1024 * 1024 + 1)
        lines = raw.splitlines()
        if (
            len(raw) > 32 * 1024 * 1024
            or len(lines) > 7204
            or any(len(line) > 131072 for line in lines)
        ):
            raise ValueError("timing_input_limit")
        rows = [json.loads(line) for line in lines]
        if not rows or rows[0].get("type") != "start" or rows[-1].get("type") != "complete_capture":
            raise ValueError("incomplete_capture")
        report = summarize([r["observation"] for r in rows if r.get("type") == "sample"])
        report["input_sha256"] = hashlib.sha256(raw).hexdigest()
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print("Timing report unavailable: " + type(exc).__name__)
        return 2
    print("Backend timing observations: " + report["status"] + "; acceptance unproven")
    return 2 if report["status"] in {"blocked", "fail"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
