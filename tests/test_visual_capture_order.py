"""Synthetic images arrive across sources out of capture order."""

import pytest
from test_visual_store import image

from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V3", "V6", "V9")
@pytest.mark.scenario("VISUAL-CAPTURE-ORDER-EVICTION")
@pytest.mark.parametrize("capacity", ["frames", "bytes"])
@pytest.mark.parametrize("pinned", [False, True])
def test_late_old_capture_cannot_evict_newer_evidence(capacity, pinned):
    store = VisualStore(clock=lambda: 100)
    camera = store.source("pc", "camera", "Camera")
    screen = store.source("pc", "screen", "Screen")
    prepared = image()
    newest = store.add(camera.id, 0, 99, prepared)
    if capacity == "frames":
        store.max_frames = 2
    else:
        store.max_bytes = 2 * newest.size
    old = store.add(screen.id, 0, 97, prepared)
    if pinned:
        store.pin(old.id, "Earlier screen")
    middle = store.add(screen.id, 0, 98, prepared)
    if pinned:
        late = store.add(camera.id, 0, 96, prepared, support=True)
        assert store.get(old.id).pin == "Earlier screen"
    else:
        late = old
    assert store.get(newest.id) is newest
    assert store.get(middle.id) is middle
    with pytest.raises(ToolError, match="expired"):
        store.get(late.id)
    assert store.totals()["rolling_frames"] == 2
    assert store.totals()["rolling_bytes"] <= store.max_bytes
    assert [row["captured"] for row in store.browse()["frames"]] == (
        [99, 98, 97] if pinned else [99, 98]
    )


@pytest.mark.features("V1", "V4", "V9")
@pytest.mark.scenario("VISUAL-RECENT-CAPTURE-ORDER")
def test_latest_and_pagination_use_capture_time_with_late_support_frames():
    store = VisualStore(clock=lambda: 100)
    camera = store.source("pc", "camera", "Camera")
    screen = store.source("pc", "screen", "Screen")
    prepared = image()
    newest = store.add(camera.id, 0, 99, prepared)
    old = store.add(screen.id, 0, 97, prepared)
    middle = store.add(camera.id, 0, 98, prepared, support=True)
    assert store.browse(source=camera.id, limit=1)["frames"][0]["id"] == newest.id
    first = store.browse(limit=2)
    assert [row["id"] for row in first["frames"]] == [newest.id, middle.id]
    second = store.browse(cursor=first["next_cursor"], limit=2)
    assert [row["id"] for row in second["frames"]] == [old.id]
    assert second["next_cursor"] is None
