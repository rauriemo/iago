"""Actual HTTP image admission with held decoder threads; synthetic prepared images."""

import asyncio
import threading
import time

import httpx
import pytest

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("V1", "V9", "C2", "D5")
@pytest.mark.scenario("FRAME-INGRESS-HELD-DECODE-CAPACITY")
@pytest.mark.parametrize("action", ["complete", "cancel", "clear"])
async def test_decoder_capacity_survives_request_cancellation(tmp_path, monkeypatch, action):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    visual = app.state.visual
    sources = [visual.source("synthetic", kind, kind) for kind in ("camera", "screen", "upload")]
    release = threading.Event()
    entered = asyncio.Queue()
    loop = asyncio.get_running_loop()
    calls = []

    def held(data, *, preserve_png=False):
        calls.append(data)
        loop.call_soon_threadsafe(entered.put_nowait, True)
        assert release.wait(5)
        return 1, 1, b"synthetic-prepared", b"synthetic-thumbnail", "a" * 64, 0.0, {}

    monkeypatch.setattr(visual, "prepare", held)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer test"},
        ) as client,
    ):

        def request(source):
            return client.post(
                f"/api/frame/{source.id}/{source.generation}",
                content=b"synthetic-input",
                headers={"x-captured-at": str(time.time())},
            )

        tasks = []
        try:
            for source in sources[:2]:
                tasks.append(asyncio.create_task(request(source)))
                await asyncio.wait_for(entered.get(), 2)
            if action == "cancel":
                tasks[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[0]
            elif action == "clear":
                visual.clear(sources[0].id, disable=True)
            excess = await asyncio.wait_for(request(sources[2]), 1)
            assert excess.status_code == 409 and excess.json()["error"] == "image_ingress_busy"
            same = await asyncio.wait_for(request(sources[0]), 1)
            assert same.status_code == 409
            assert len(calls) == 2
            assert (await asyncio.wait_for(client.get("/api/behaviors"), 1)).status_code == 200
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        if action == "clear":
            assert tasks[0].result().status_code == 409
        # Await actual worker completion/release, not just the canceled request.
        for _ in range(100):
            result = await request(sources[2])
            if result.status_code != 409:
                break
            await asyncio.sleep(0.01)
        assert result.status_code == 200
        assert len(visual.frames) == (3 if action == "complete" else 2)
        assert sources[2].upload_accepted == 1
        assert sources[2].upload_rejected >= 1
        assert sources[2].upload_attempts == sources[2].upload_accepted + sources[2].upload_rejected
        assert sources[1].upload_attempts == sources[1].upload_accepted == 1
        assert sources[1].upload_rejected == 0
        if action == "cancel":
            assert sources[0].upload_accepted == 0 and sources[0].upload_rejected == 2


@pytest.mark.features("V1", "V9", "D5")
@pytest.mark.scenario("FRAME-INGRESS-EARLY-METADATA-REJECTION")
@pytest.mark.parametrize("case", ["missing_source", "stale_generation", "bad_length", "nan_time"])
async def test_invalid_metadata_never_enters_decoder(tmp_path, monkeypatch, case):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    source = app.state.visual.source("synthetic", "camera", "Camera")
    calls = []

    def forbidden(data):
        calls.append(data)
        raise AssertionError("invalid metadata reached decoder")

    monkeypatch.setattr(app.state.visual, "prepare", forbidden)
    headers = {"Authorization": "Bearer test", "x-captured-at": str(time.time())}
    if case == "bad_length":
        headers["content-length"] = "invalid"
    if case == "nan_time":
        headers["x-captured-at"] = "nan"
    identity = "missing" if case == "missing_source" else source.id
    generation = 1 if case == "stale_generation" else 0
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client,
    ):
        result = await client.post(
            f"/api/frame/{identity}/{generation}", content=b"synthetic", headers=headers
        )
        assert result.status_code in (400, 409)
        assert not calls and not app.state.visual.frames
        counted = 1 if case in {"bad_length", "nan_time"} else 0
        assert source.upload_attempts == source.upload_rejected == counted
        assert source.upload_accepted == 0


@pytest.mark.features("V1", "V9", "D5")
@pytest.mark.scenario("FRAME-UPLOAD-COUNTERS-GENERATION")
def test_late_upload_completion_cannot_pollute_reset_generation():
    from reachy_brain.vision.store import VisualStore

    visual = VisualStore(clock=lambda: 100)
    source = visual.source("synthetic", "camera", "Camera")
    with visual.upload_attempt(source.id, source.generation):
        assert source.upload_attempts == 1
        visual.clear(source.id)
        assert source.upload_attempts == 0
    assert source.upload_accepted == source.upload_rejected == 0
    with pytest.raises(ValueError), visual.upload_attempt(source.id, source.generation):
        raise ValueError("synthetic rejected upload")
    assert source.upload_attempts == source.upload_rejected == 1
