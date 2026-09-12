"""Bounded backend speech activity; neither audio duration nor echo classification."""

import time
import uuid
from collections import deque


class SpeechActivity:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.owner = uuid.uuid4().hex
        self.samples = deque(maxlen=512)
        self.counts = {"onset": 0, "uncertain_onset": 0, "accepted_speech": 0}

    def record(self, kind, epoch):
        if kind not in self.counts:
            raise ValueError("invalid_speech_activity")
        self.counts[kind] += 1
        self.samples.append(
            {
                "sequence": sum(self.counts.values()),
                "kind": kind,
                "epoch": epoch,
                "seconds": max(0, self.clock() - self.started),
            }
        )

    def snapshot(self):
        total = sum(self.counts.values())
        return {
            "owner": self.owner,
            "elapsed_seconds": max(0, self.clock() - self.started),
            "counts": dict(self.counts),
            "samples": [dict(s) for s in self.samples],
            "sample_limit": 512,
            "dropped_samples": total - len(self.samples),
            "scope": "Backend onset notifications and accepted speech turns since owner creation. No transcript/audio retained. Duplicate notifications count; no echo classification, physical playback duration or pre-notification capture coverage. Resets when the owner is recreated.",
        }
