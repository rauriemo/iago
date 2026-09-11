"""Actual control/HTTP wiring with held storage and canceled caller; synthetic input."""

import asyncio
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "C2", "D5")
@pytest.mark.scenario("PROJECT-TRANSITION-FENCES-NEW-INPUT")
@pytest.mark.parametrize("action", ["activate", "remove"])
@pytest.mark.parametrize("cancel_caller", [False, True])
def test_project_transition_stays_guarded_until_storage_finishes(
    tmp_path, monkeypatch, action, cancel_caller
):
    root = tmp_path / "originals"
    root.mkdir()
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), token="test")
    projects = app.state.projects
    project = projects.add_project("Synthetic", root)
    projects.activate(project)
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as control,
    ):
        control.send_text("test")
        assert control.receive_json()["type"] == "ready"
        core = app.state.active["conversation"]

        async def exercise():
            core.mode = "conversation"
            core.history.append({"role": "user", "content": "Old project context"})
            entered, release = asyncio.Event(), threading.Event()
            loop = asyncio.get_running_loop()
            original = getattr(projects, action)

            def held(*args):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
                return original(*args)

            monkeypatch.setattr(projects, action, held)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer test"},
            ) as http:
                request = asyncio.create_task(
                    http.post("/api/projects", json={"action": action, "project": project})
                )
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    workers = [
                        task
                        for task in asyncio.all_tasks()
                        if task.get_name().startswith("project-change-")
                    ]
                    assert len(workers) == 1
                    if cancel_caller:
                        request.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await request
                    assert not core.project_context_ready() and not core.valid(core.epoch)
                    await core.user_turn(
                        "Question during project change",
                        kind="speech" if cancel_caller else "typed",
                    )
                    assert not core.history and core.task is None
                    assert not workers[0].done()
                finally:
                    release.set()
                    if not request.cancelled():
                        await request
                    if "workers" in locals():
                        await asyncio.gather(*workers)
                assert core.project_context_ready() and core.valid(core.epoch)
                assert projects.active == (project if action == "activate" else None)
                await core.stop()

        client.portal.call(exercise)


@pytest.mark.features("K1", "C2", "D5")
@pytest.mark.scenario("PROJECT-TRANSITION-DURING-TURN-STOP")
@pytest.mark.parametrize("transition", ["pending", "completed", "away_and_back"])
async def test_transition_started_during_turn_stop_is_rechecked(transition):
    messages = []

    async def send(message):
        messages.append(message)

    core = Conversation(Settings(_env_file=None), None, {}, None, None, send)
    core.mode = "conversation"
    ready = True
    generation = 0
    core.project_context_ready = lambda: ready
    core.project_context_generation = lambda: generation
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_stop():
        entered.set()
        await release.wait()

    core.stop = held_stop
    request = asyncio.create_task(core.user_turn("Synthetic question"))
    await entered.wait()
    ready = transition != "pending"
    generation += 2 if transition == "away_and_back" else 1
    release.set()
    await request
    assert not core.history and core.task is None
    assert messages[-1]["type"] == "error" and "Project context changed" in messages[-1]["message"]
