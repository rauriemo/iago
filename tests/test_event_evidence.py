"""Synthetic image evidence tests preserve provenance and shared history limits."""

import io

import pytest
from PIL import Image

from reachy_brain.behavior.engine import Event
from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.evidence import EventEvidence
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("P1", "P3", "P4", "V2", "V3")
@pytest.mark.scenario("EVENT-SUPPORT-FRAME-BOUNDS")
async def test_delayed_support_preserves_high_water_and_global_budget():
    store = VisualStore(max_bytes=4000, clock=lambda: 10)
    source = store.source("owner", "camera", "Synthetic")
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(out, "JPEG")
    jpeg = out.getvalue()
    store.add(source.id, 0, 10, store.prepare(jpeg))
    retention = EventEvidence(store)
    events = [
        Event(str(i), source.id, kind, 9.5, 0.95)
        for i, kind in enumerate(["wave_detected", "person_entered_view"])
    ]
    bound = await retention.attach(events, jpeg, uncertainty=1)
    assert bound[0].frames == bound[1].frames and len(bound[0].frames) == 1
    frame = store.get(bound[0].frames[0])
    assert frame.captured == 9.5 and not frame.capture_time_known
    assert source.last_capture == 10
    with pytest.raises(ToolError, match="stale_frame"):
        store.add(source.id, 0, 9.7, store.prepare(jpeg))
    suppressed = await retention.attach(
        [Event("fast", source.id, "wave_detected", 9.6, 0.95)], jpeg
    )
    assert suppressed[0].frames == ()
    await retention.attach([Event("next", source.id, "wave_detected", 10, 0.95)], jpeg)
    assert store.totals()["rolling_bytes"] <= 4000
    store.clear(source.id, disable=True)
    assert not store.frames
    with pytest.raises(ToolError, match="stale_source"):
        await retention.attach([Event("stale", source.id, "wave_detected", 10.6, 0.95)], jpeg)
