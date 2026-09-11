"""Per-adapter failure backoff; canceled speech is not a provider failure."""

import time
from contextlib import contextmanager


class CircuitOpen(Exception):
    pass


class SpeechCircuit:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.failures = 0
        self.until = 0.0
        self.probing = False
        self.generation = 0

    def check(self):
        if self.until and (self.clock() < self.until or self.probing):
            raise CircuitOpen()

    @contextmanager
    def attempt(self):
        self.check()
        generation = self.generation
        probe = bool(self.until)
        if probe:
            self.probing = True
        try:
            yield
        except Exception:
            if generation == self.generation:
                self.failures += 1
                if probe or self.failures >= 3:
                    self.until = self.clock() + 30
                    self.generation += 1
                    self.probing = False
            raise
        else:
            if generation == self.generation:
                self.failures = 0
                self.until = 0.0
                if probe:
                    self.generation += 1
                    self.probing = False
        finally:
            if generation == self.generation and probe:
                self.probing = False

    def status(self):
        return {
            "state": "probe_in_progress" if self.probing else "open" if self.until else "closed",
            "failures": self.failures,
            "retry_after_seconds": max(0.0, self.until - self.clock()),
        }
