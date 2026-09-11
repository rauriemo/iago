"""Finite robot-local cues; one owner and shared stop lock, no model/plugin dependency."""

import math

import numpy as np


class MotionCues:
    def __init__(self, media, lock):
        self.media, self.lock = media, lock
        self.enabled = False
        self.active = None
        self.settle_until = 0

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("invalid_motion_setting")
        with self.lock:
            if not enabled:
                self.active = None
                self.media.hold()
            self.enabled = enabled
            self.media.motion_enabled = enabled

    def cue(self, kind, valid, *, now):
        presets = {
            "acknowledge": (0.05, 0.8, 1),
            "listening": (0.025, 1.0, 1),
            "speaking": (0.035, 1.2, 2),
        }
        if kind not in presets:
            raise ValueError("invalid_motion_cue")
        with self.lock:
            if not self.enabled or self.active or not valid():
                return False
            base = np.asarray(self.media.pose(), dtype=np.float64).copy()
            if base.shape != (4, 4) or not np.isfinite(base).all():
                raise ValueError("invalid_current_pose")
            self.active = (now, presets[kind], base, valid)
            self.settle_until = now + presets[kind][1] + 0.6
            return True

    def stop(self, *, now):
        with self.lock:
            if self.active:
                self.settle_until = max(self.settle_until, now + 0.6)
            self.active = None

    def tick(self, *, now):
        with self.lock:
            if not self.active:
                return
            started, (amplitude, duration, cycles), base, valid = self.active
            if not self.enabled or not valid():
                self.active = None
                self.media.hold()
                return
            progress = min(1, max(0, (now - started) / duration))
            angle = (
                amplitude * math.sin(math.pi * progress) * math.sin(2 * math.pi * cycles * progress)
            )
            rotation = np.eye(4)
            c, s = math.cos(angle), math.sin(angle)
            rotation[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
            self.media.target(base @ rotation)
            if progress >= 1:
                self.active = None
