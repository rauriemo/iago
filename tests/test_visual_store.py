"""Synthetic images and controlled clocks, never optical quality evidence."""

import base64
import hashlib
import io
import json

import pytest
from PIL import Image

from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.store import VisualStore


def image():
    data = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(data, format="PNG")
    return VisualStore.prepare(data.getvalue())


@pytest.mark.features("V3", "V4", "D5")
@pytest.mark.scenario("PIN-LABEL-BYTE-ACCOUNTING")
def test_pin_labels_count_utf8_and_rename_is_atomic():
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    frame = store.add(source.id, 0, 99, image())
    base = frame.size
    store.pin_bytes = base + 4
    store.pin(frame.id, "éé")
    assert frame.size == base + 4
    assert store.totals()["pin_bytes"] == base + 4
    with pytest.raises(ToolError, match="pin_limit"):
        store.pin(frame.id, "ééé")
    assert frame.pin == "éé"
    with pytest.raises(ToolError, match="invalid_pin_label"):
        store.pin(frame.id, ["bad"])
    assert frame.pin == "éé"
    store.pin(frame.id, "a")
    assert store.totals()["pin_bytes"] == base + 1
    store.unpin(frame.id)
    assert frame.size == base
    store.pin_bytes = base
    with pytest.raises(ToolError, match="pin_limit"):
        store.pin(frame.id, "a")
    assert not frame.pin


@pytest.mark.features("V1", "V4", "V9")
@pytest.mark.scenario("VISUAL-STORED-IMAGE-HASH")
def test_image_hash_identifies_retained_original_and_crop_parent():
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "screen", "Screen")
    prepared = image()
    frame = store.add(source.id, 0, 99, prepared)
    expected = hashlib.sha256(frame.image).hexdigest()
    assert store.describe(frame)["image_sha256"] == expected
    original = base64.b64decode(store.image_input(frame.id)["image_url"].split(",")[1])
    assert hashlib.sha256(original).hexdigest() == expected
    crop = base64.b64decode(store.image_input(frame.id, [0, 0, 40, 40])["image_url"].split(",")[1])
    assert hashlib.sha256(crop).hexdigest() != expected
    store.pin(frame.id, "Reference")
    assert store.describe(frame)["image_sha256"] == expected
    assert hashlib.sha256(frame.thumbnail).hexdigest() != expected


@pytest.mark.features("V1", "V9")
@pytest.mark.scenario("VISUAL-NONFINITE-CAPTURE-REJECTION")
@pytest.mark.parametrize("captured", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_capture_cannot_enter_history(captured):
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    with pytest.raises(ToolError, match="stale_frame"):
        store.add(source.id, source.generation, captured, image())
    assert not store.frames


@pytest.mark.features("V1", "V3", "V6", "V9", "D5")
@pytest.mark.scenario("VISUAL-FRAME-COUNT-BOUND")
def test_tiny_frames_cannot_grow_metadata_and_pins_remain_separate():
    now = [100.0]
    store = VisualStore(max_frames=3, clock=lambda: now[0], retention=600)
    cam = store.source("synthetic", "camera", "Camera")
    screen = store.source("synthetic", "screen", "Screen")
    prepared = image()
    pinned = store.add(cam.id, 0, 90, prepared)
    store.pin(pinned.id, "keep")
    added = []
    for index in range(20):
        source = cam if index % 2 == 0 else screen
        added.append(store.add(source.id, 0, 91 + index / 100, prepared))
    assert list(store.frames) == [pinned.id, *(f.id for f in added[-3:])]
    assert store.totals()["rolling_frames"] == 3
    assert store.totals()["rolling_frame_limit"] == 3
    with pytest.raises(ToolError, match="expired"):
        store.get(added[0].id)
    store.unpin(pinned.id)
    assert pinned.id not in store.frames
    assert len(store.frames) == 3
    now[0] = 1000
    store.expire()
    assert not store.frames


@pytest.mark.features("V3", "V6", "V9", "D4")
@pytest.mark.scenario("VISUAL-RETAINED-RANGE")
def test_retained_range_excludes_pins_and_expires_before_reporting():
    now = [100.0]
    store = VisualStore(clock=lambda: now[0], retention=10)
    cam = store.source("synthetic", "camera", "Camera")
    screen = store.source("synthetic", "screen", "Screen")
    prepared = image()
    pinned = store.add(cam.id, 0, 91, prepared)
    store.pin(pinned.id, "old board")
    store.add(cam.id, 0, 95, prepared)
    store.add(screen.id, 0, 98, prepared)
    totals = store.totals()
    assert totals["earliest_rolling_capture"] == 95
    assert totals["latest_rolling_capture"] == 98
    now[0] = 108
    totals = store.totals()
    assert totals["earliest_rolling_capture"] is None
    assert totals["latest_rolling_capture"] is None
    assert totals["frames"] == totals["pins"] == 1


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
    frame_bytes = (
        len(prepared[2])
        + len(prepared[3])
        + len(json.dumps(prepared[6], separators=(",", ":")).encode("utf-8"))
    )
    store = VisualStore(max_bytes=frame_bytes * 2, clock=lambda: 100)
    cam, screen = store.source("x", "camera", "C"), store.source("x", "screen", "S")
    for t, src in [(98, cam), (98, screen), (99, cam), (99, screen), (100, cam), (100, screen)]:
        store.add(src.id, 0, t, prepared)
    assert len(store.frames) == 2
    assert store.totals()["rolling_bytes"] <= frame_bytes * 2
    assert {f.source for f in store.frames.values()} == {cam.id, screen.id}
