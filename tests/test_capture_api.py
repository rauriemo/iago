"""Actual capture HTTP/executor path with synthetic camera data; no physical claims."""

import asyncio
import io
import time
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import Rule
from reachy_brain.web.app import create_app


@pytest.mark.features("V4", "D2", "D3", "E1")
@pytest.mark.scenario("EXPLICIT-CAPTURE-HTTP-POLICY-INVALIDATION")
@pytest.mark.parametrize("action", ["complete", "stop", "clear", "deny"])
async def test_capture_http_uses_executor_and_rejects_stale_results(tmp_path, action):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="synthetic")
    async with app.router.lifespan_context(app):
        core = SimpleNamespace(mode="aware", epoch=0, session="synthetic")
        app.state.active["conversation"] = core
        visual = app.state.visual
        source = visual.source("synthetic", "camera", "Synthetic Reachy source")
        data = io.BytesIO()
        Image.new("RGB", (20, 20), "orange").save(data, format="PNG")
        prepared = visual.prepare(data.getvalue())
        old = visual.add(source.id, 0, time.time() - 1, prepared)
        requested = asyncio.Event()
        visual.capture_hooks[source.id] = lambda: requested.set() or True
        if action == "deny":
            app.state.executor.policy.set(Rule("visual__session__capture_now", "read", "deny"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer synthetic"},
        ) as client:
            task = asyncio.create_task(client.post(f"/api/capture?source={source.id}"))
            try:
                if action == "deny":
                    response = await asyncio.wait_for(task, 1)
                    assert response.status_code == 409
                    assert not requested.is_set()
                else:
                    await asyncio.wait_for(requested.wait(), 1)
                    assert not task.done()
                    # The waiting capture does not monopolize the server event loop.
                    image = await asyncio.wait_for(client.get(f"/api/frame/{old.id}"), 1)
                    assert image.status_code == 200
                    if action == "stop":
                        core.epoch += 1
                    elif action == "clear":
                        visual.clear(source.id)
                    fresh = visual.add(source.id, source.generation, time.time(), prepared)
                    response = await asyncio.wait_for(task, 2)
                    if action == "complete":
                        assert response.status_code == 200
                        assert response.json()["id"] == fresh.id
                        assert response.json()["image_sha256"] == fresh.image_sha256
                    else:
                        assert response.status_code == 409
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                app.state.active["conversation"] = None
