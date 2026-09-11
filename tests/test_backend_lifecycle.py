"""Startup/cleanup failures must unwind independently acquired resources; no billing."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("D1", "D2", "D3", "E1")
@pytest.mark.scenario("BACKEND-PARTIAL-STARTUP-CLEANUP")
@pytest.mark.parametrize("failure", ["integration", "edge", "shutdown"])
async def test_startup_and_shutdown_unwind_resources(tmp_path, monkeypatch, failure):
    brain = SimpleNamespace(close=AsyncMock())
    speech = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr("reachy_brain.web.app.AstraBrain", lambda *a: brain)
    monkeypatch.setattr("reachy_brain.web.app.OpenAISpeech", lambda *a: speech)
    closed = AsyncMock()

    class Robot:
        def __init__(self, *args, **kwargs):
            pass

        async def send(self, message):
            pass

        async def start(self):
            if failure == "edge":
                raise RuntimeError("synthetic_edge_start_failure")

        async def close(self):
            await closed()
            if failure == "shutdown":
                raise RuntimeError("synthetic_edge_close_failure")

    monkeypatch.setattr("reachy_brain.web.app.RobotSession", Robot)
    settings = Settings(_env_file=None, data_dir=tmp_path, deployment_mode="reachy_pc")
    if failure == "integration":
        settings.integration_config = tmp_path / "missing-installation.json"
    app = create_app(settings, token="synthetic")
    with pytest.raises(FileNotFoundError if failure == "integration" else RuntimeError):
        async with app.router.lifespan_context(app):
            assert failure == "shutdown"
    brain.close.assert_awaited_once()
    speech.close.assert_awaited_once()
    assert closed.await_count == (0 if failure == "integration" else 1)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        app.state.executor.operations.recent()
