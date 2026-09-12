"""Fresh archive arrival through the real executor; synthetic pixels, no physical claim."""

import asyncio
import base64
import io

import pytest
from PIL import Image

from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import ActionPolicy, CallContext, ToolExecutor, ToolRegistry
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V1", "V4", "V9", "E1")
@pytest.mark.scenario("VISUAL-CAPTURE-NEW-ARRIVAL")
@pytest.mark.parametrize("action", ["fresh", "clear", "cancel", "timeout", "upload"])
async def test_capture_now_requires_new_live_arrival(tmp_path, monkeypatch, action):
    now = [100.0]
    visual = VisualStore(clock=lambda: now[0])
    source = visual.source("synthetic", "upload" if action == "upload" else "screen", "Source")
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(data, format="PNG")
    prepared = visual.prepare(data.getvalue())
    old = visual.add(source.id, 0, 99.9, prepared)
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, visual, None, Notes(tmp_path / "notes.sqlite"))
    executor = ToolExecutor(registry, policy, None)
    ctx = CallContext("synthetic", 0)
    requests = []
    visual.capture_hooks[source.id] = lambda: requests.append(source.id) or True
    entered = asyncio.Event()
    browse = visual.browse

    def observed(**kwargs):
        result = browse(**kwargs)
        entered.set()
        return result

    monkeypatch.setattr(visual, "browse", observed)
    task = asyncio.create_task(
        executor.execute("visual__session__capture_now", {"source": source.id}, ctx)
    )
    if action == "upload":
        result = await asyncio.wait_for(task, 1)
        assert result["status"] == "unavailable_live_source"
        assert requests == []
        return
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done(), "must not return the pre-request archive frame"
    assert requests == [source.id]
    if action == "fresh":
        now[0] = 100.1
        fresh = visual.add(source.id, 0, 100.1, prepared)
        result = await asyncio.wait_for(task, 1)
        assert result["status"] == "ok"
        assert ctx.evidence[0]["id"] == fresh.id != old.id
        assert ctx.evidence[0]["captured"] >= 100
        assert len(ctx.attachments) == 2
    elif action == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not ctx.attachments

    else:
        if action == "clear":
            visual.clear(source.id)
        result = await asyncio.wait_for(task, 3)
        assert result["status"] == (
            "stale_source" if action == "clear" else "unavailable_fresh_frame"
        )
        assert not ctx.attachments


@pytest.mark.features("V1", "V4", "V9", "E1")
@pytest.mark.scenario("VISUAL-CAPTURE-DETAIL-REGION")
@pytest.mark.parametrize(
    "options,expected,size",
    [
        ({}, "ok", (640, 480)),
        ({"detail": "preview"}, "ok", (320, 240)),
        ({"detail": "full", "region": [10, 20, 30, 40]}, "ok", (30, 40)),
        ({"region": [630, 0, 30, 40]}, "invalid_region", None),
        ({"region": [0, 0, 0, 40]}, "invalid_region", None),
        ({"detail": "preview", "region": [0, 0, 30, 40]}, "invalid_region", None),
        ({"region": [0, 0, 30]}, "invalid_input", None),
        ({"detail": "invented"}, "invalid_input", None),
    ],
)
async def test_capture_detail_region(tmp_path, options, expected, size):
    visual = VisualStore(clock=lambda: 100.0)
    source = visual.source("synthetic", "camera", "Synthetic camera")
    data = io.BytesIO()
    Image.new("RGB", (640, 480), "blue").save(data, format="PNG")
    prepared = visual.prepare(data.getvalue())
    requests = []

    def arrive():
        requests.append(visual.add(source.id, 0, 100.0, prepared))
        return True

    visual.capture_hooks[source.id] = arrive
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, visual, None, Notes(tmp_path / "notes.sqlite"))
    executor = ToolExecutor(registry, policy, None)
    ctx = CallContext("synthetic", 0)
    result = await executor.execute(
        "visual__session__capture_now", {"source": source.id, **options}, ctx
    )
    assert result["status"] == expected
    if expected == "ok":
        assert len(requests) == 1
        evidence = ctx.evidence[0]
        assert evidence["id"] == requests[0].id
        assert evidence["image_sha256"] == requests[0].image_sha256
        assert evidence.get("region") == options.get("region")
        encoded = ctx.attachments[1]["image_url"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            assert image.size == size
        detail = options.get("detail", "full")
        assert result["result"]["detail"] == detail
        assert ctx.budgets["previews" if detail == "preview" else "details"] == 1
    else:
        assert not ctx.attachments and not ctx.evidence
        # Only bounds relative to the newly captured image require an arrival.
        assert len(requests) == (1 if options.get("region") == [630, 0, 30, 40] else 0)
