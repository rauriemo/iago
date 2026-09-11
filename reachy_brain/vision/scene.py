"""Bounded spatial-change detector; persistence and motion suppression precede events."""

import math

import numpy as np
from PIL import Image


class SceneChange:
    def __init__(self, *, threshold=0.12, persistence=0.6, cooldown=3):
        self.threshold, self.persistence, self.cooldown = threshold, persistence, cooldown
        self.baseline = self.candidate = None
        self.since = None
        self.last = self.last_event = -math.inf

    def update(self, rgb, at, *, moving):
        if not math.isfinite(at) or at <= self.last:
            return None
        gap = at - self.last > 0.5
        self.last = at
        if moving or gap:
            self.baseline = self.candidate = None
            self.since = None
        if moving:
            return None
        small = (
            np.asarray(
                Image.fromarray(rgb).resize((16, 12), Image.Resampling.BOX), dtype=np.float32
            )
            / 255
        )
        # Suppress a uniform exposure shift; retain spatial layout changes.
        descriptor = small - small.mean(axis=(0, 1), keepdims=True)
        if self.baseline is None:
            self.baseline = descriptor
            return None
        difference = float(np.mean(np.abs(descriptor - self.baseline)))
        if difference < self.threshold:
            self.candidate = None
            self.since = None
            return None
        if self.candidate is None or float(np.mean(np.abs(descriptor - self.candidate))) > 0.04:
            self.candidate, self.since = descriptor, at
            return None
        duration = at - self.since
        if duration < self.persistence or at - self.last_event < self.cooldown:
            return None
        self.baseline, self.candidate, self.since = descriptor, None, None
        self.last_event = at
        return {
            "duration": duration,
            "score": min(1.0, difference / (2 * self.threshold)),
            "spatial_difference": difference,
        }
