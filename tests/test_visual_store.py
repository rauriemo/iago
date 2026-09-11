"""Synthetic images and controlled clocks, never optical quality evidence."""

import io

import pytest
from PIL import Image

from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.store import VisualStore


def image():
    data = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(data, format="PNG")
    return VisualStore.prepare(data.getvalue())


@pytest.mark.features("V1", "V3", "V6", "V9")
@pytest.mark.scenario("VISUAL-SOURCE-LIFECYCLE")
def test_sources_pins_generation_and_expiry():
    now = [100.0]
    store = VisualStore(clock=lambda: now[0], retention=10, pin_count=1)
    cam = store.source("browser", "camera", "Camera")
    screen = store.source("browser", "screen", "Screen")
    a = store.add(cam.id, 0, 100, image())
    b = store.add(screen.id, 0, 100, image())
    store.pin(a.id, "board")
    with pytest.raises(ToolError, match="pin_limit"):
        store.pin(b.id, "screen")
    store.clear(screen.id, disable=True)
    assert store.get(a.id).pin == "board"
    with pytest.raises(ToolError, match="stale_source"):
        store.add(screen.id, 0, 101, image())
    now[0] = 120
    assert store.get(a.id).pin
    store.unpin(a.id)
    assert not store.frames
    store.clear()
    with pytest.raises(ToolError, match="stale_source"):
        store.add(cam.id, 0, 120, image())


@pytest.mark.features("V3", "V9")
@pytest.mark.scenario("VISUAL-GLOBAL-BUDGET")
def test_shared_budget_not_multiplied_by_sources():
    prepared = image()
    frame_bytes = len(prepared[2]) + len(prepared[3])
    store = VisualStore(max_bytes=frame_bytes * 2, clock=lambda: 100)
    cam, screen = store.source("x", "camera", "C"), store.source("x", "screen", "S")
    for t, src in [(98, cam), (98, screen), (99, cam), (99, screen), (100, cam), (100, screen)]:
        store.add(src.id, 0, t, prepared)
    assert len(store.frames) == 2
    assert store.totals()["rolling_bytes"] <= frame_bytes * 2
    assert {f.source for f in store.frames.values()} == {cam.id, screen.id}
