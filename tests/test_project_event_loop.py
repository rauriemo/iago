"""Real HTTP handlers with held project work and independent event-loop traffic."""

import asyncio
import threading

import httpx
import pytest

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "D5", "C2")
@pytest.mark.scenario("PROJECT-CONTROLS-LEAVE-EVENT-LOOP-FREE")
@pytest.mark.parametrize(
    "action,method", [("status", "status"), ("activate", "activate"), ("refresh", "status")]
)
async def test_held_project_work_does_not_block_other_http_traffic(
    tmp_path, monkeypatch, action, method
):
    root = tmp_path / "originals"
    root.mkdir()
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), token="test")
    projects = app.state.projects
    project = projects.add_project("Synthetic", root)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    original = getattr(projects, method)

    def held(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3), "project work blocked event-loop progress"
        return original(*args, **kwargs)

    async with app.router.lifespan_context(app):
        monkeypatch.setattr(projects, method, held)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer test"},
        ) as client:
            request = asyncio.create_task(
                client.get("/api/status")
                if action == "status"
                else client.post("/api/projects", json={"action": action, "project": project})
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert not request.done()
                response = await asyncio.wait_for(client.get("/api/behaviors"), 1)
                assert response.status_code == 200
                assert not request.done()
            finally:
                release.set()
                result = await request
            assert result.status_code == 200
