"""Actual private HTTP exports with held worker barriers; no provider/device calls."""

import asyncio
import importlib
import threading
import uuid

import httpx
import pytest

from reachy_brain.config import Settings
from reachy_brain.storage.transcripts import Transcripts

web = importlib.import_module("reachy_brain.web.app")

pytestmark = [
    pytest.mark.features("C2", "C9", "D5"),
    pytest.mark.scenario("TRANSCRIPT-EXPORT-WORKER-ISOLATION"),
]


@pytest.mark.parametrize("format", ["json", "text"])
async def test_export_workers_do_not_block_controls_or_release_early(tmp_path, monkeypatch, format):
    app = web.create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    store = Transcripts(tmp_path / "transcripts.sqlite")
    state = store.set_enabled(True)
    session = uuid.uuid4().hex
    store.record(
        session, "one", "user", "Ação: saved idea", "typed", generation=state["generation"]
    )
    render = web.transcript_export_response
    loop_thread = threading.get_ident()
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    count = [0]
    lock = threading.Lock()

    def held(*args):
        assert threading.get_ident() != loop_thread, "Export rendering blocked the event loop"
        with lock:
            index = count[0]
            count[0] += 1
        if index < 2:
            entered[index].set()
            assert release.wait(5), "Test did not release export worker"
        return render(*args)

    monkeypatch.setattr(web, "transcript_export_response", held)
    requests = []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer test"},
        ) as client,
    ):
        url = f"/api/transcripts/{session}/export?format={format}"
        try:
            for i in range(2):
                requests.append(asyncio.create_task(client.get(url)))
                assert await asyncio.to_thread(entered[i].wait, 2)
            # The actual preferences endpoint can run while both exports are stalled.
            response = await asyncio.wait_for(client.get("/api/transcripts"), 0.5)
            assert response.status_code == 200 and response.json()["entries"] == 1
            assert (await client.get(url)).status_code == 429
            requests[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await requests[0]
            assert (await client.get(url)).status_code == 429
            assert count[0] == 2
            release.set()
            response = await asyncio.wait_for(requests[1], 2)
            assert response.status_code == 200
            assert "Ação: saved idea" in response.text
            if format == "json":
                assert response.json()["entries"][0]["text"] == "Ação: saved idea"
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            missing = f"/api/transcripts/{uuid.uuid4().hex}/export?format={format}"
            assert (await client.get(missing)).status_code == 404
            assert (await client.get(url)).status_code == 200
        finally:
            release.set()
            await asyncio.gather(*requests, return_exceptions=True)


@pytest.mark.parametrize("format", ["json", "text"])
@pytest.mark.parametrize("action", ["delete", "delete_all", "disable"])
async def test_pending_export_rejects_changed_transcript_state(
    tmp_path, monkeypatch, format, action
):
    app = web.create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    store = Transcripts(tmp_path / "transcripts.sqlite")
    state = store.set_enabled(True)
    session = uuid.uuid4().hex
    store.record(
        session, "one", "user", "Private old snapshot", "typed", generation=state["generation"]
    )
    rendered, release = threading.Event(), threading.Event()
    original = web.transcript_export_response

    def hold_completed_response(*args):
        result = original(*args)
        rendered.set()
        assert release.wait(5), "Test did not release rendered export"
        return result

    monkeypatch.setattr(web, "transcript_export_response", hold_completed_response)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer test"},
        ) as client,
    ):
        url = f"/api/transcripts/{session}/export?format={format}"
        pending = asyncio.create_task(client.get(url))
        try:
            assert await asyncio.to_thread(rendered.wait, 2)
            payload = (
                {"action": "enabled", "enabled": False}
                if action == "disable"
                else {"action": action, "session": session}
            )
            changed = await asyncio.wait_for(client.post("/api/transcripts", json=payload), 1)
            assert changed.status_code == 200
            release.set()
            response = await asyncio.wait_for(pending, 2)
            assert response.status_code == 409
            assert "Private old snapshot" not in response.text
            fresh = await client.get(url)
            assert fresh.status_code == (200 if action == "disable" else 404)
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
