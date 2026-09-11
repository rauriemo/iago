"""Synthetic spatial patterns test temporal rules, not real scene accuracy."""

import numpy as np
import pytest

from reachy_brain.vision.scene import SceneChange


@pytest.mark.features("P2", "P4", "V2")
@pytest.mark.scenario("SCENE-SETTLED-PERSISTENCE")
def test_stable_change_once_transient_exposure_and_motion_suppressed():
    baseline = np.zeros((120, 160, 3), dtype=np.uint8)
    baseline[:, :80] = 180
    changed = np.flip(baseline, axis=1).copy()
    detector = SceneChange()
    assert detector.update(baseline, 0, moving=False) is None
    assert detector.update(changed, 0.1, moving=False) is None
    assert detector.update(baseline, 0.2, moving=False) is None
    assert detector.update(baseline + 20, 0.3, moving=False) is None
    observed = []
    for at in [0.4, 0.6, 0.8, 1.01, 1.2, 1.4]:
        result = detector.update(changed, at, moving=False)
        if result:
            observed.append(result)
    assert len(observed) == 1 and observed[0]["duration"] >= 0.6
    assert detector.update(baseline, 1.5, moving=True) is None
    for at in [1.6, 1.8, 2, 2.2, 2.4]:
        assert detector.update(baseline, at, moving=False) is None


@pytest.mark.features("P2", "P4")
@pytest.mark.scenario("SCENE-TRACKING-GAP-RESET")
def test_gap_requires_new_baseline_and_old_timestamp_cannot_trigger():
    detector = SceneChange()
    first = np.zeros((12, 16, 3), dtype=np.uint8)
    second = first.copy()
    second[:, :8] = 255
    detector.update(first, 1, moving=False)
    assert detector.update(second, 2, moving=False) is None
    assert detector.update(first, 1.5, moving=False) is None
    for at in [2.1, 2.3, 2.5, 2.7]:
        assert detector.update(second, at, moving=False) is None
