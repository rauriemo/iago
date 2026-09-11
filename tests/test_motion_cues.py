"""Synthetic poses and clock qualify scheduling/stop policy, not physical motion."""

import threading

import numpy as np
import pytest

from reachy_brain.robot.motion import MotionCues


@pytest.mark.features("P9", "D2", "D3", "C2")
@pytest.mark.scenario("MOTION-CUE-STOP-OWNERSHIP")
def test_cues_are_finite_disabled_by_default_and_stop_rejects_continuation():
    class Media:
        motion_enabled = False
        targets = []
        holds = 0

        def pose(self):
            return np.eye(4)

        def target(self, pose):
            self.targets.append(pose.copy())

        def hold(self):
            self.holds += 1

    media = Media()
    motion = MotionCues(media, threading.RLock())
    valid = [True]
    assert not motion.cue("acknowledge", lambda: valid[0], now=0)
    motion.configure(True)
    assert motion.cue("acknowledge", lambda: valid[0], now=0)
    assert not motion.cue("speaking", lambda: True, now=0)
    motion.tick(now=0.2)
    assert not np.allclose(media.targets[-1], np.eye(4))
    valid[0] = False
    count = len(media.targets)
    motion.tick(now=0.3)
    assert len(media.targets) == count and media.holds == 1
    assert motion.active is None
    assert motion.cue("listening", lambda: True, now=1)
    motion.tick(now=2)
    assert np.allclose(media.targets[-1], np.eye(4)) and motion.active is None
    assert motion.cue("speaking", lambda: True, now=3)
    motion.stop(now=3.1)
    count = len(media.targets)
    motion.tick(now=3.2)
    assert len(media.targets) == count
    motion.configure(False)
    assert not media.motion_enabled
