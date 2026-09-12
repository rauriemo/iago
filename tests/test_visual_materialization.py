"""Held image workers through actual tool execution; synthetic images, no physical claim."""

import asyncio
import base64
import io
import threading
import time

import pytest
from PIL import Image

from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import ActionPolicy, CallContext, ToolExecutor, ToolRegistry
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V2", "V4", "C2", "D5", "E1")
@pytest.mark.scenario("VISUAL-WORKER-CANCELLATION-CAPACITY")
@pytest.mark.parametrize("clear", [False, True])
async def test_canceled_materialization_keeps_capacity_until_thread_finishes(
    tmp_path, monkeypatch, clear
):
    visual = VisualStore()
    source = visual.source("synthetic", "camera", "Camera")
    data = io.BytesIO()
    Image.new("RGB", (80, 60)).save(data, format="PNG")
    prepared = visual.prepare(data.getvalue())
    frames = [visual.add(source.id, 0, time.time() - 10 + i, prepared) for i in range(3)]
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, visual, None, Notes(tmp_path / "notes.sqlite"))
    executor = ToolExecutor(registry, policy, None)
    release = threading.Event()
    entered = asyncio.Queue()
    loop = asyncio.get_running_loop()
    original = visual.encode_image

    def held(snapshot):
        loop.call_soon_threadsafe(entered.put_nowait, True)
        assert release.wait(5)
        return original(snapshot)

    monkeypatch.setattr(visual, "encode_image", held)
    contexts = [CallContext("synthetic", 0) for _ in frames]
    tasks = []
    try:
        for index in (0, 1):
            tasks.append(
                asyncio.create_task(
                    executor.execute(
                        "visual__session__inspect_region",
                        {"id": frames[index].id, "region": [1, 2, 20, 10]},
                        contexts[index],
                    )
                )
            )
            await asyncio.wait_for(entered.get(), 2)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        busy = await asyncio.wait_for(
            executor.execute(
                "visual__session__inspect_frames", {"ids": [frames[2].id]}, contexts[2]
            ),
            1,
        )
        assert busy["status"] == "image_ingress_busy"
        assert visual.totals()["model_image_workers"] == 2
        assert not contexts[0].attachments and not contexts[2].attachments
        if clear:
            visual.clear(source.id, disable=True)
        release.set()
        result = await tasks[1]
        assert result["status"] == ("expired" if clear else "ok")
        assert bool(contexts[1].attachments) is (not clear)
        if not clear:
            encoded = contexts[1].attachments[-1]["image_url"].split(",", 1)[1]
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as crop:
                assert crop.size == (20, 10)
        fresh_source = visual.source("synthetic", "upload", "Fresh")
        fresh = visual.add(fresh_source.id, 0, time.time(), prepared)
        resumed = await executor.execute(
            "visual__session__inspect_frames", {"ids": [fresh.id]}, CallContext("synthetic", 1)
        )
        assert resumed["status"] == "ok"
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await executor.close()
