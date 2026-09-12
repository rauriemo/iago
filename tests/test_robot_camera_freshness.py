"""Controlled native snapshot clock/encoding; no physical camera claim."""

import threading

import numpy as np
import pytest
from PIL import Image

from reachy_brain.robot.camera import CameraFeed


@pytest.mark.features("V5", "D2", "D3", "D5")
@pytest.mark.scenario("ROBOT-CAMERA-SNAPSHOT-FRESHNESS")
@pytest.mark.parametrize("phase", ["cached", "encoding"])
@pytest.mark.parametrize("now", [99, 100.5, 102])
def test_snapshot_freshness_before_cache_and_after_encoding(monkeypatch, phase, now):
    feed = CameraFeed.__new__(CameraFeed)
    feed.lock = threading.Lock()
    feed.ready = threading.Event()
    feed.ready.set()
    feed.enabled = True
    feed.generation = 1
    feed.latest = (np.zeros((20, 20, 3), dtype=np.uint8), 100, 1, 1)
    cached = (b"synthetic-cached", {"sequence": 1, "retrieved": 100})
    feed.encoded = {False: cached} if phase == "cached" else {}
    clock = [now if phase == "cached" else 100]
    monkeypatch.setattr("reachy_brain.robot.camera.time.time", lambda: clock[0])
    save = Image.Image.save

    def encode(image, *args, **kwargs):
        save(image, *args, **kwargs)
        clock[0] = now

    monkeypatch.setattr(Image.Image, "save", encode)
    result = feed.snapshot()
    if now == 100.5:
        assert result is not None and result[1]["sequence"] == 1
    else:
        assert result is None
        if phase == "encoding":
            assert not feed.encoded
