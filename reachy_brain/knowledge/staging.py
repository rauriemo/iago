"""Bounded temporary scan storage; retain only one extracted file in memory."""

import json
import tempfile
import threading
from contextlib import contextmanager

from reachy_brain.integrations.registry import ToolError


class StagingBudget:
    def __init__(self, directory, limit):
        self.directory, self.limit = directory, limit
        self.used = 0
        self.lock = threading.Lock()

    @contextmanager
    def open(self):
        stage = None
        try:
            with tempfile.TemporaryFile(
                mode="w+b", dir=self.directory, prefix=".iago-index-"
            ) as file:
                stage = ScanStage(self, file)
                yield stage
        finally:
            if stage is not None:
                with self.lock:
                    self.used -= stage.size


class ScanStage:
    # Parser stdout is capped at 2 MiB; allow bounded record metadata around it.
    record_limit = 3 * 1024 * 1024

    def __init__(self, budget, file):
        self.budget, self.file = budget, file
        self.size = 0

    def append(self, record):
        encoded = json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n"
        with self.budget.lock:
            if (
                len(encoded) > self.record_limit
                or self.budget.used + len(encoded) > self.budget.limit
            ):
                raise ToolError("index_staging_capacity")
            self.budget.used += len(encoded)
            self.size += len(encoded)
        self.file.write(encoded)

    def __iter__(self):
        self.file.seek(0)
        while line := self.file.readline(self.record_limit + 1):
            if len(line) > self.record_limit:
                raise ToolError("index_staging_capacity")
            yield json.loads(line)
