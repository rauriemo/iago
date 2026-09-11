"""Synthetic connections verify bounded recovery and absence of old media replay."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from reachy_brain.config import Settings
from reachy_brain.robot.session import RobotSession


@pytest.mark.features("D2", "D3", "D5", "C2")
@pytest.mark.scenario("ROBOT-BOUNDED-RECONNECT")
@pytest.mark.parametrize("succeed", [False, True])
async def test_fresh_clients_bounded_attempts_idle_recovery(succeed):
    clients, commands = [], []

    class Client:
        error = None
        stop_generation = 0

        def __init__(self, *args, **kwargs):
            self.on_event = kwargs.get("on_event")
            self.closed = False
            clients.append(self)

        async def start(self):
            if not succeed or len(clients) < 4:
                raise RuntimeError("synthetic_connection_refused")

        async def command(self, kind, **payload):
            commands.append(kind)
            if kind == "audio_settings":
                return {**payload, "input_generation": 7}
            return {}

        async def close(self):
            self.closed = True

    core = SimpleNamespace(
        connection="old", set_mode=AsyncMock(), stop_generation=12, user_speaking=False
    )
    notify = AsyncMock()
    session = RobotSession(Settings(_env_file=None), None, core, notify, client_factory=Client)
    session.recovery_delays = (0, 0, 0)
    session.marks.append((1, 4, "old-unheard"))
    session.speech_event_ids.append(("speech_end", 9))
    assert session.reconnect() and not session.reconnect()
    await session.reconnect_task
    assert not session.reconnect(automatic=True)
    assert len(clients) == 4 and all(c.closed for c in clients[:3])
    assert not session.marks and not session.speech_event_ids
    core.set_mode.assert_awaited_once_with("idle")
    if succeed:
        assert commands == ["mode", "motion_enabled", "camera", "audio_settings"]
        assert core.connection != "old" and core.stop_generation == 0
        assert session.input_generation == 7 and session.error is None
        assert not session.motion_enabled
    else:
        assert not commands and all(c.closed for c in clients)
        assert session.error == "reconnect_exhausted"
    await session.close()
    assert not session.reconnect()


@pytest.mark.features("D2", "D3", "C2")
@pytest.mark.scenario("ROBOT-RECONNECT-END-CANCEL")
async def test_end_cancels_inflight_reconnection_without_late_activation():
    import asyncio

    entered = asyncio.Event()
    clients = []

    class Client:
        error = None
        closed = False

        def __init__(self, *args, **kwargs):
            clients.append(self)

        async def start(self):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

        async def command(self, *args, **kwargs):
            raise RuntimeError("No mode command should be sent on canceled startup")

    core = SimpleNamespace(set_mode=AsyncMock())
    session = RobotSession(Settings(_env_file=None), None, core, AsyncMock(), client_factory=Client)
    session.recovery_delays = (0, 0, 0)
    session.reconnect()
    await asyncio.wait_for(entered.wait(), 2)
    with pytest.raises(RuntimeError, match="edge_reconnecting"):
        await session.set_mode("conversation")
    await session.set_mode("idle")
    assert session.reconnect_task.cancelled()
    assert len(clients) == 2 and all(c.closed for c in clients)
    assert clients[-1].error == "reconnect_canceled"
    await session.close()
