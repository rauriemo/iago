"""Injected provider failures/cancellation exercise real session cleanup without billing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from reachy_brain.config import Settings
from reachy_brain.robot.session import RobotSession


@pytest.mark.features("D2", "D3", "D5", "C2")
@pytest.mark.scenario("ROBOT-MODE-FAILURE-RELEASE")
@pytest.mark.parametrize("failure", ["provider", "cancel"])
async def test_mode_setup_failure_releases_old_capture_and_closes_stt(monkeypatch, failure):
    calls = []
    started = asyncio.Event()

    class STT:
        def __init__(self, *args):
            self.closed = False

        async def start(self):
            calls.append("stt_start")
            started.set()
            if failure == "provider":
                raise RuntimeError("synthetic_provider_failure")
            await asyncio.Event().wait()

        async def close(self):
            calls.append("stt_close")
            self.closed = True

    class Edge:
        error = None

        async def command(self, kind, **payload):
            calls.append(payload.get("mode", kind))
            return {"generation": 1}

    async def stop(**kwargs):
        calls.append("stop")

    core = SimpleNamespace(
        session_ended=None,
        record_session=Mock(),
        mode="conversation",
        stop=stop,
        select_voice=AsyncMock(),
        thumbs=SimpleNamespace(invalidate=Mock()),
        visual=SimpleNamespace(clear=Mock()),
    )
    monkeypatch.setattr("reachy_brain.robot.session.Transcription", STT)
    session = RobotSession(
        Settings(_env_file=None), None, core, AsyncMock(), client_factory=lambda *a, **k: Edge()
    )
    old_task = session.spawn(asyncio.Event().wait())
    task = asyncio.create_task(session.set_mode("conversation"))
    await started.wait()
    if failure == "cancel":
        task.cancel()
    with pytest.raises(RuntimeError if failure == "provider" else asyncio.CancelledError):
        await task
    assert calls == ["stop", "idle", "stt_start", "stt_close", "idle"]
    assert old_task.cancelled() and session.stt is None and core.mode == "idle"
    core.visual.clear.assert_called_once_with(disable=True)
    core.thumbs.invalidate.assert_called_once_with("mode_transition_failed")
