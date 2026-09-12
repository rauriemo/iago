"""Check declared activity coverage; never authenticate or qualify physical evidence."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ActiveWindow(StrictRecord):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    camera: bool
    screen: bool
    waves: bool
    thumbs: bool
    supported_document_files: int = Field(ge=0)


class Activity(StrictRecord):
    id: str = Field(min_length=1, max_length=128)
    at: float = Field(ge=0)
    end: float | None = Field(default=None, ge=0)
    kind: Literal[
        "query_camera",
        "query_screen",
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
    ]
    evidence: str = Field(min_length=1, max_length=256)
    cycle: str = Field(default="", max_length=128)
    module: str = Field(default="", max_length=128)
    account: str = Field(default="", max_length=128)
    project: str = Field(default="", max_length=128)
    transport: Literal["", "stdio", "streamable-http"] = ""


class WorkloadRun(StrictRecord):
    version: Literal[1] = 1
    origin: Literal["synthetic", "reported-real"]
    duration: float = Field(gt=0, le=7200)
    windows: list[ActiveWindow] = Field(max_length=1000)
    activities: list[Activity] = Field(max_length=10000)

    @model_validator(mode="after")
    def coherent(self):
        if len({event.id for event in self.activities}) != len(self.activities):
            raise ValueError("duplicate_activity_id")
        if any(w.end <= w.start or w.end > self.duration for w in self.windows):
            raise ValueError("invalid_active_interval")
        previous = None
        state_fields = ("camera", "screen", "waves", "thumbs", "supported_document_files")
        for window in sorted(self.windows, key=lambda item: item.start):
            if previous is not None and window.start < previous.end:
                if any(getattr(previous, field) != getattr(window, field) for field in state_fields):
                    raise ValueError("contradictory_active_windows")
                if previous.end >= window.end:
                    continue
            previous = window
        if any(
            event.at > self.duration
            or event.end is not None
            and (event.end < event.at or event.end > self.duration)
            for event in self.activities
        ):
            raise ValueError("invalid_activity_interval")
        return self


def merged_windows(windows):
    merged = []
    for window in sorted(windows, key=lambda value: value.start):
        if (
            not all((window.camera, window.screen, window.waves, window.thumbs))
            or window.supported_document_files < 30
        ):
            continue
        if merged and window.start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], window.end)
        else:
            merged.append([window.start, window.end])
    return merged


def cadence(times, start, end, maximum):
    times = sorted(time for time in times if start <= time <= end)
    return (
        bool(times)
        and max(b - a for a, b in zip([start] + times, times + [end], strict=True)) <= maximum
    )


def assess(run: WorkloadRun):
    failures = []
    events = sorted(run.activities, key=lambda event: event.at)
    windows = merged_windows(run.windows)
    counts = Counter(event.kind for event in events)
    if run.duration < 5400:
        failures.append("ninety_minute_duration")
    coverage = sum(end - start for start, end in windows)
    if coverage < 3600:
        failures.append("sixty_minute_combined_activity")
    for start, end in windows:
        queries = [e for e in events if e.kind.startswith("query_") and start <= e.at <= end]
        if not cadence([e.at for e in queries], start, end, 300):
            failures.append("five_minute_query_cadence")
        if len(queries) < 3 or any(
            len({e.kind for e in queries[index : index + 3]}) != 3
            for index in range(len(queries) - 2)
        ):
            failures.append("camera_screen_document_alternation")
        if not cadence([e.at for e in events if e.kind == "reindex"], start, end, 600):
            failures.append("ten_minute_reindex_cadence")
    for kind in ("edit", "move", "delete", "create"):
        if counts[kind] < 3:
            failures.append("three_" + kind + "_mutations")
    mutations = [e for e in events if e.kind in {"edit", "move", "delete", "create"}]
    if any(not e.project for e in mutations) or len({e.project for e in mutations}) < 3:
        failures.append("mutations_across_three_projects")
    for kind in ("thumb", "interruption"):
        if counts[kind] < 20:
            failures.append("twenty_" + kind + "_events")
    cycles = defaultdict(list)
    for event in events:
        if event.kind.startswith("calendar_"):
            cycles[event.cycle].append(event)
    completed = []
    for cycle_id, steps in cycles.items():
        if (
            not cycle_id
            or [e.kind for e in steps] != ["calendar_read", "calendar_draft", "calendar_write"]
            or len({e.transport for e in steps}) != 1
            or not steps[0].transport
            or not steps[0].module.strip()
            or not steps[0].account.strip()
            or len({(e.module, e.account) for e in steps}) != 1
            or any(
                (previous.end if previous.end is not None else previous.at) > following.at
                for previous, following in zip(steps, steps[1:], strict=False)
            )
        ):
            failures.append("invalid_calendar_cycle")
        else:
            write = steps[-1]
            completed.append((write.end if write.end is not None else write.at, steps[0].transport))
    completed.sort()
    if not cadence([at for at, _ in completed], 0, run.duration, 600):
        failures.append("ten_minute_calendar_cadence")
    if len(completed) < 2 or any(
        a[1] == b[1] for a, b in zip(completed, completed[1:], strict=False)
    ):
        failures.append("alternating_calendar_transports")
    interruptions = [e.at for e in events if e.kind == "interruption"]
    for kind in ("delayed_call", "reconciliation"):
        if not any(
            e.kind == kind
            and e.end is not None
            and any(e.at <= at <= e.end for at in interruptions)
            for e in events
        ):
            failures.append(kind + "_during_interruption")
    return {
        "origin": run.origin,
        "declared_activity_coverage_met": not failures,
        "failures": sorted(set(failures)),
        "duration": run.duration,
        "combined_activity_seconds": coverage,
        "counts": dict(counts),
        "calendar_cycles": len(completed),
        "physical_qualification": False,
        "acceptance_pass": False,
        "remaining": [
            "Authenticate and review referenced activity evidence and valid accepted turns",
            "Verify twelve required document operations and corpus across all three projects",
            "Measure storage/queue/journal/index limits and memory after warmup",
            "Provide required timing distributions and both-voice/fallback physical acoustic gates",
            "Complete all other SPEC and ACCEPTANCE requirements; this is coverage only",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.input.open("rb") as source:
            data = source.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("workload_trace_limit")
        report = assess(WorkloadRun.model_validate_json(data))
        report["input_sha256"] = hashlib.sha256(data).hexdigest()
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, indent=2) + "\n")
    except Exception as exc:
        parser.exit(2, f"Coverage evaluation unavailable ({type(exc).__name__}).\n")
    print(
        "Declared activity coverage: "
        + ("met" if report["declared_activity_coverage_met"] else "not met")
    )
    print("Physical qualification and complete acceptance remain unproven.")
    return 0 if report["declared_activity_coverage_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
