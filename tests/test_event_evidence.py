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


@pytest.mark.features("V3", "V4", "V6", "P2", "E1")
@pytest.mark.scenario("EVENT-METADATA-PROVENANCE-BOUNDS")
@pytest.mark.parametrize(
    "variant,code",
    [
        ("source", "mixed_event_evidence"),
        ("generation", "mixed_event_evidence"),
        ("time", "mixed_event_evidence"),
        ("count", "event_metadata_limit"),
        ("label", "invalid_event_metadata"),
        ("bytes", "result_limit"),
    ],
)
async def test_invalid_event_batch_never_decodes(monkeypatch, variant, code):
    from dataclasses import replace

    store = VisualStore(clock=lambda: 10)
    source = store.source("synthetic", "camera", "Camera")
    base = Event("first", source.id, "object_appeared", 10, 0.9)
    if variant == "source":
        events = [base, replace(base, source="other")]
    elif variant == "generation":
        events = [base, replace(base, generation=1)]
    elif variant == "time":
        events = [base, replace(base, captured=9.9)]
    elif variant == "count":
        events = [base] * 33
    elif variant == "label":
        events = [replace(base, details={"label": ["phone"]})]
    else:
        events = [replace(base, id=str(i), details={"label": "界" * 80}) for i in range(8)]

    def forbidden_decode(*args):
        raise AssertionError("invalid metadata must fail before image work")

    monkeypatch.setattr(store, "prepare", forbidden_decode)
    with pytest.raises(ToolError, match=code):
        await EventEvidence(store).attach(events, b"not decoded")
    assert not store.frames


@pytest.mark.features("V3", "V4", "V6", "P2")
@pytest.mark.scenario("EVENT-METADATA-SHARED-BYTE-CAP")
async def test_event_metadata_counts_toward_image_and_pin_limits():
    import json

    out = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(out, "JPEG")
    jpeg = out.getvalue()
    store = VisualStore(clock=lambda: 10)
    source = store.source("synthetic", "camera", "Camera")
    events = [Event("one", source.id, "object_appeared", 10, 0.9, details={"label": "phone"})]
    result = await EventEvidence(store).attach(events, jpeg)
    frame = store.get(result[0].frames[0])
    expected = len(json.dumps({"labels": frame.labels, "events": frame.events}).encode())
    assert frame.event_metadata_bytes == expected > 0
    transformation_bytes = len(json.dumps(frame.transformation, separators=(",", ":")).encode())
    assert store.totals()["rolling_bytes"] == (
        len(frame.image) + len(frame.thumbnail) + expected + transformation_bytes
    )
    store.pin(frame.id, "Explicit pin")
    assert store.totals()["pin_bytes"] == frame.size
    store.unpin(frame.id)
    store.max_bytes = frame.size - 1
    store.expire()
    assert not store.frames
    # A frame that cannot fit after event metadata is attached must not yield a usable ID.
    with pytest.raises(ToolError, match="expired"):
        await EventEvidence(store).attach(events, jpeg)
    assert not store.frames
