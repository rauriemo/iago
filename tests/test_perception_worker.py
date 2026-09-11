"""Actual isolated model process on synthetic frames; no physical perception claims."""

import io
import time
from pathlib import Path

import pytest
from PIL import Image

from reachy_brain.vision.worker import PerceptionWorker


@pytest.mark.features("P1", "P2", "P10", "D5")
@pytest.mark.scenario("PERCEPTION-PROCESS-LATEST")
def test_isolated_latest_frame_worker_lifecycle():
    image = Image.new("RGB", (640, 480), "black")
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG")
    worker = PerceptionWorker(Path("local-data/models"))
    try:
        deadline = time.monotonic() + 10
        result = None
        while time.monotonic() < deadline:
            worker.offer("camera-test", 4, time.time(), encoded.getvalue())
            time.sleep(0.1)
            result = worker.poll()
            if result:
                break
        assert result and "error" not in result
        assert (result["source"], result["generation"]) == ("camera-test", 4)
        assert result["objects"] == [] and result["hands"] == []
        assert result["seconds"] > 0
        deadline = time.monotonic() + 10
        replacement = None
        while time.monotonic() < deadline:
            worker.offer("camera-test", 5, time.time(), encoded.getvalue())
            time.sleep(0.1)
            replacement = worker.poll()
            if replacement and replacement.get("generation") == 5:
                break
        assert replacement and replacement["generation"] == 5
        assert replacement["tracking_resets"] == 1
        assert replacement["objects"] == [] and replacement["hands"] == []
        deadline = time.monotonic() + 3
        settled = None
        while time.monotonic() < deadline:
            worker.offer("camera-test", 5, time.time(), encoded.getvalue())
            time.sleep(0.1)
            settled = worker.poll()
            if settled and not settled.get("moving", True):
                break
        assert settled and not settled["moving"]
        marker = time.time()
        moved = None
        while time.monotonic() < deadline:
            worker.offer("camera-test", 5, time.time(), encoded.getvalue(), moving=True)
            time.sleep(0.1)
            moved = worker.poll()
            if moved and moved.get("captured", 0) >= marker:
                break
        assert moved and moved["moving"] and moved["scene_change"] is None
    finally:
        worker.close()
    assert not worker.process.is_alive()
