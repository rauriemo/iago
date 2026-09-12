"""Content-free per-response backend milestones; never a physical playout measurement."""

import asyncio
import math
import time
import uuid
from collections import OrderedDict, deque
from contextlib import contextmanager


class ResponseTimings:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.owner = uuid.uuid4().hex
        self.rows = OrderedDict()
        self.sequence = 0

    def begin(self, epoch, origin="other", *, started=None, recognition=None):
        if epoch in self.rows:
            return
        self.sequence += 1
        self.rows[epoch] = {
            "start": self.clock() if started is None else started,
            "sequence": self.sequence,
            "origin": origin if origin in {"typed", "speech", "gesture", "initiative"} else "other",
            "stages": {},
            "status": "running",
            "recognition": dict(recognition) if recognition is not None else None,
        }
        while len(self.rows) > 128:
            self.rows.popitem(last=False)

    def mark(self, epoch, stage):
        row = self.rows.get(epoch)
        if row is None or row["status"] != "running":
            return
        if stage not in {
            "model_start",
            "first_model_text",
            "first_audio_dispatch",
            "retrieval_requested",
            "other_tool_requested",
            "voice_fallback",
            "speech_failed",
        }:
            raise ValueError("invalid_response_timing_stage")
        row["stages"].setdefault(stage, max(0, self.clock() - row["start"]))

    def finish(self, epoch, status):
        if status not in {"finished", "canceled", "error", "speech_failed"}:
            raise ValueError("invalid_response_timing_status")
        row = self.rows.get(epoch)
        if row is not None and row["status"] == "running":
            row["status"] = status
            row["stages"]["ended"] = max(0, self.clock() - row["start"])

    def snapshot(self):
        return {
            "owner": self.owner,
            "total": self.sequence,
            "samples": [
                {
                    "sequence": r["sequence"],
                    "origin": r["origin"],
                    "status": r["status"],
                    "stages": dict(r["stages"]),
                    "recognition": dict(r["recognition"]) if r["recognition"] is not None else None,
                }
                for r in self.rows.values()
            ],
            "sample_limit": 128,
            "scope": "Seconds from accepted input (or answer start for other origins), on this conversation owner's backend clock. First audio dispatch excludes network transfer and physical playout; no microphone endpoint or ordinary audible-latency qualification. Latest 128 responses; resets with owner recreation.",
        }


class InputTimings:
    """Bounded commit/item correlation; raw provider IDs never appear in exported data."""

    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.commits = OrderedDict()
        self.items = OrderedDict()

    def start(self, commit):
        self.commits[commit] = self.clock()
        while len(self.commits) > 32:
            self.commits.popitem(last=False)

    def abandon(self, commit):
        self.commits.pop(commit, None)

    def item(self, key):
        if not isinstance(key, str) or not 0 < len(key) <= 128:
            return None
        row = self.items.setdefault(key, {})
        while len(self.items) > 64:
            self.items.popitem(last=False)
        return row

    def bind(self, item, commit):
        start = self.commits.pop(commit, None)
        row = self.item(item)
        if row is not None:
            row.update(start=start, acknowledged=self.clock())

    def complete(self, item):
        row = self.item(item)
        if row is not None:
            row["completed"] = self.clock()

    def release(self, item):
        row = self.items.pop(item, None)
        if row is None or row.get("start") is None or "completed" not in row:
            return {"status": "unavailable"}
        now, start = self.clock(), row["start"]
        if min(row["acknowledged"], row["completed"]) < start or now < max(
            row["acknowledged"], row["completed"]
        ):
            return {"status": "invalid_timeline"}
        return {
            "status": "measured",
            "commit_to_ack_seconds": row["acknowledged"] - start,
            "commit_to_transcript_seconds": row["completed"] - start,
            "commit_to_release_seconds": now - start,
            "reorder_wait_seconds": now - row["completed"],
        }

    def clear(self):
        self.commits.clear()
        self.items.clear()


class StageTimings:
    """Bounded event-loop measurements with fixed caller-owned category names."""

    def __init__(self, operations, scope, clock=time.perf_counter):
        self.operations, self.scope, self.clock = frozenset(operations), scope, clock
        self.samples = deque(maxlen=64)
        self.sequence = 0

    def record(self, operation, seconds, outcome="ok"):
        if operation not in self.operations or outcome not in {"ok", "error", "canceled"}:
            raise ValueError("invalid_stage_timing_category")
        self.sequence += 1
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or seconds < 0:
            return False  # Missing sequence is visible; never fabricate zero latency.
        self.samples.append(
            {
                "sequence": self.sequence,
                "operation": operation,
                "outcome": outcome,
                "seconds": seconds,
            }
        )
        return True

    @contextmanager
    def measure(self, operation):
        started, outcome = self.clock(), "ok"
        try:
            yield
        except asyncio.CancelledError:
            outcome = "canceled"
            raise
        except BaseException:
            outcome = "error"
            raise
        finally:
            self.record(operation, self.clock() - started, outcome)

    def snapshot(self):
        return {
            "samples": [dict(row) for row in self.samples],
            "total": self.sequence,
            "sample_limit": 64,
            "scope": self.scope,
        }
