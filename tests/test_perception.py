"""Real model smoke checks on blank synthetic images; temporal checks use synthetic tracks."""

from pathlib import Path

import numpy as np
import pytest

from reachy_brain.vision.detectors import LocalDetectors
from reachy_brain.vision.temporal import Presence, Wave


@pytest.mark.features("P1", "P2", "P3", "P10")
@pytest.mark.scenario("PERCEPTION-REAL-MODEL-SMOKE")
def test_real_models_infer_blank_image_without_fake_detections():
    with LocalDetectors(Path("local-data/models")) as detectors:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        assert detectors.objects(image, 1) == []
        assert detectors.hands(image, 1) == []
        assert "person" in detectors.labels and "cell phone" in detectors.labels
        with pytest.raises(ValueError, match="timestamp"):
            detectors.hands(image, 1)


@pytest.mark.features("P1", "P3", "P6")
@pytest.mark.scenario("PERCEPTION-TEMPORAL-NOT-STATIC")
def test_wave_requires_reversals_and_presence_rearms_after_absence():
    wave = Wave()
    for i in range(30):
        assert not wave.update(i * 0.1, 0.5, 0.1, open_palm=True, confident=True)
    wave = Wave()
    events = []
    for i, x in enumerate(
        [0.4, 0.42, 0.46, 0.5, 0.52, 0.5, 0.46, 0.42, 0.4, 0.42, 0.46, 0.5, 0.52]
    ):
        if wave.update(i * 0.1, x, 0.1, open_palm=True, confident=True):
            events.append(i)
    assert len(events) == 1
    wave = Wave()
    for i, x in enumerate([0.4, 0.5, 0.4, 0.5, 0.4, 0.5, 0.4, 0.5]):
        assert not wave.update(i * 0.1, x, 0.1, open_palm=True, confident=True, moving=True)
    presence = Presence()
    assert presence.update(True, 0) is None
    assert presence.update(True, 1) is None  # Startup is not an arrival.
    presence.update(False, 2)
    assert presence.update(False, 5) == "person_left_view"
    presence.update(True, 18)
    assert presence.update(True, 18.8) == "person_entered_view"
