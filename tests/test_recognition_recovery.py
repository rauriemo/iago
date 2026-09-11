"""Provider stream loss through actual app transports, using synthetic adapters."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("C1", "C2", "P7", "D5")
@pytest.mark.scenario("RECOGNITION-LOSS-AWARE")
@pytest.mark.parametrize("error", [False, True])
def test_recognition_eof_and_error_stop_cloud_input(tmp_path, monkeypatch, error):
    providers = []

    class STT:
        def __init__(self, *args):
            self.closed = False
            self.packets = []
            providers.append(self)

        async def start(self):
            pass

        async def events(self):
            await asyncio.sleep(0.1)
            if error:
                raise RuntimeError("synthetic_disconnect")
            return
            yield  # This is an async iterator that ends without a final transcript.

        async def append(self, data):
            self.packets.append(data)

        async def close(self):
            self.closed = True

    monkeypatch.setattr("reachy_brain.web.app.Transcription", STT)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app) as client, client.websocket_connect("/control", headers=origin) as control:
        control.send_text("test")
        assert control.receive_json()["type"] == "ready"
        with client.websocket_connect("/audio", headers=origin) as audio:
            audio.send_text("test")
            assert audio.receive_json()["type"] == "audio_ready"
            control.send_json({"type": "mode", "mode": "conversation"})
            assert control.receive_json()["type"] == "stop"
            assert control.receive_json()["mode"] == "conversation"
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if providers and providers[0].closed:
                    break
                time.sleep(0.01)
            assert providers[0].closed
            assert app.state.active["conversation"].mode == "aware"
            assert app.state.active["stt"] is None
            audio.send_bytes(bytes(960))
            control.send_json({"type": "heartbeat"})
            # Drain the loss state/error and reach the control barrier.
            for _ in range(8):
                if control.receive_json()["type"] == "heartbeat":
                    break
            assert not providers[0].packets


@pytest.mark.features("D2", "D3", "C1")
@pytest.mark.scenario("ROBOT-RECOGNITION-EOF")
async def test_robot_clean_eof_enters_failure_recovery():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from reachy_brain.robot.session import RobotSession

    class STT:
        async def events(self):
            return
            yield

    session = RobotSession(
        Settings(_env_file=None),
        None,
        SimpleNamespace(),
        AsyncMock(),
        client_factory=lambda *args, **kwargs: SimpleNamespace(),
    )
    session.stt = STT()
    session.fail = AsyncMock()
    await session.transcripts()
    session.fail.assert_awaited_once_with("transcription_stream_ended")
