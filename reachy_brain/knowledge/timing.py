"""Content-free bounded timings for local project work, excluding provider calls."""

import threading
import time
from collections import deque
from functools import wraps


class ProjectTimings:
    def __init__(self, clock=time.perf_counter):
        self.clock = clock
        self.lock = threading.Lock()
        self.samples = deque(maxlen=256)
        self.counts = {}
        self.sequence = 0

    def record(self, operation, start, outcome):
        sample = {
            "operation": operation,
            "seconds": max(0, self.clock() - start),
            "outcome": outcome,
        }
        with self.lock:
            self.sequence += 1
            sample["sequence"] = self.sequence
            self.samples.append(sample)
            key = operation + "/" + outcome
            self.counts[key] = self.counts.get(key, 0) + 1

    def snapshot(self):
        with self.lock:
            return {
                "samples": [dict(row) for row in self.samples],
                "counts": dict(self.counts),
                "sample_limit": 256,
                "scope": "Local worker execution including internal lock/parser waits; excludes worker-queue wait, provider reasoning, network and physical playout. Latest 256 completions; counts cover this process lifetime.",
            }


def timed(operation):
    def decorate(method):
        @wraps(method)
        def measured(self, *args, **kwargs):
            start, outcome = self.timings.clock(), "error"
            try:
                result = method(self, *args, **kwargs)
                outcome = "no_op" if result is False else "ok"
                return result
            finally:
                self.timings.record(operation, start, outcome)

        return measured

    return decorate
