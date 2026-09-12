"""Held HTTP detector uploads and fake worker; no physical inference claims."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("P1", "P3", "P10", "D5")
@pytest.mark.scenario("DETECTOR-UPLOAD-STALE-OWNER")
@pytest.mark.parametrize(
    "action", ["clear", "idle", "replace", "duplicate", "nan", "aged", "cancel"]
)
async def test_detector_upload_revalidates_owner_and_bounds(tmp_path, monkeypatch, action):
    offers = []

    class Worker:
        def __init__(self, *args):
            pass

        def poll(self):
            return None

        def close(self):
            pass

        def offer(self, *args, **kwargs):
            offers.append(args)

    monkeypatch.setattr("reachy_brain.web.app.PerceptionWorker", Worker)
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, perception_enabled=True), token="synthetic"
    )
    async with app.router.lifespan_context(app):
        core = SimpleNamespace(mode="aware")
        app.state.active["conversation"] = core
        source = app.state.visual.source("synthetic", "camera", "Camera")
        entered, release = asyncio.Event(), asyncio.Event()

        async def body():
            entered.set()
            yield b"first"
            await release.wait()
            yield b"last"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer synthetic"},
        ) as client:
            url = f"/api/perception/{source.id}/{source.generation}"
            task = None
            try:
                if action == "nan":
                    response = await client.post(
                        url, content=b"synthetic", headers={"x-captured-at": "nan"}
                    )
                    assert response.status_code == 400
                else:
                    task = asyncio.create_task(
                        client.post(
                            url, content=body(), headers={"x-captured-at": str(time.time())}
                        )
                    )
                    await asyncio.wait_for(entered.wait(), 2)
                    if action == "clear":
                        app.state.visual.clear(source.id, disable=True)
                    if action == "idle":
                        core.mode = "idle"
                    if action == "replace":
                        app.state.active["conversation"] = SimpleNamespace(mode="aware")
                    if action == "aged":
                        original_time = time.time
                        monkeypatch.setattr(time, "time", lambda: original_time() + 2)
                    if action == "cancel":
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                        response = await client.post(
                            url, content=b"retry", headers={"x-captured-at": str(time.time())}
                        )
                        assert response.status_code == 200
                        assert len(offers) == 1
                        return
                    if action == "duplicate":
                        duplicate = await client.post(
                            url, content=b"second", headers={"x-captured-at": str(time.time())}
                        )
                        assert duplicate.status_code == 409
                    release.set()
                    response = await asyncio.wait_for(task, 2)
                    assert response.status_code == (200 if action == "duplicate" else 409)
                assert len(offers) == (1 if action == "duplicate" else 0)
            finally:
                release.set()
                if task:
                    await asyncio.gather(task, return_exceptions=True)
                app.state.active["conversation"] = None


@pytest.mark.features("V5", "P3", "P10")
@pytest.mark.scenario("DETECTOR-CLOCK-UNCERTAINTY-INGRESS")
@pytest.mark.parametrize("case", ["precise", "uncertain", "missing", "wrong_owner", "nan"])
async def test_detector_preserves_clock_uncertainty(tmp_path, monkeypatch, case):
    offers = []

    class Worker:
        def __init__(self, *args):
            pass

        def poll(self):
            return None

        def close(self):
            pass

        def offer(self, *args, **kwargs):
            offers.append((args, kwargs))

    monkeypatch.setattr("reachy_brain.web.app.PerceptionWorker", Worker)
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, perception_enabled=True), token="test"
    )
    async with app.router.lifespan_context(app):
        app.state.active["conversation"] = SimpleNamespace(mode="aware")
        source = app.state.visual.source("synthetic", "camera", "Camera")
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer test"},
            ) as client:
                owner = (await client.get("/api/clock")).json()["owner"]
                headers = {"X-Captured-At": str(time.time())}
                if case != "missing":
                    headers.update(
                        {
                            "X-Clock-Owner": owner,
                            "X-Source-Monotonic": "10",
                            "X-Clock-Uncertainty": "0.04" if case == "precise" else "0.2",
                        }
                    )
                if case == "wrong_owner":
                    headers["X-Clock-Owner"] = "f" * 32
                if case == "nan":
                    headers["X-Clock-Uncertainty"] = "nan"
                response = await client.post(
                    f"/api/perception/{source.id}/0", headers=headers, content=b"synthetic"
                )
                if case in ("wrong_owner", "nan"):
                    assert response.status_code == 400 and not offers
                else:
                    assert response.status_code == 200 and len(offers) == 1
                    assert (
                        offers[0][1]["uncertainty"]
                        == {"precise": 0.04, "uncertain": 0.2, "missing": 1.0}[case]
                    )
        finally:
            app.state.active["conversation"] = None


@pytest.mark.features("V5", "P3", "P10")
@pytest.mark.scenario("DETECTOR-SEQUENCE-HTTP-REPLAY")
async def test_detector_replay_and_sequence_downgrade_do_not_reach_worker(tmp_path, monkeypatch):
    offers = []

    class Worker:
        def __init__(self, *args):
            pass

        def poll(self):
            return None

        def close(self):
            pass

        def offer(self, *args, **kwargs):
            offers.append(kwargs)

    monkeypatch.setattr("reachy_brain.web.app.PerceptionWorker", Worker)
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, perception_enabled=True), token="test"
    )
    async with app.router.lifespan_context(app):
        app.state.active["conversation"] = SimpleNamespace(mode="aware")
        source = app.state.visual.source("synthetic", "camera", "Camera")
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer test"},
            ) as client:
                for sequence, status in [(2, 200), (2, 409), (1, 409), (None, 409), (3, 200)]:
                    headers = {"X-Captured-At": str(time.time())}
                    if sequence is not None:
                        headers["X-Frame-Sequence"] = str(sequence)
                    response = await client.post(
                        f"/api/perception/{source.id}/0", content=b"synthetic", headers=headers
                    )
                    assert response.status_code == status
                assert [o["sequence"] for o in offers] == [2, 3]
                assert source.last_frame_sequence == -1
        finally:
            app.state.active["conversation"] = None
