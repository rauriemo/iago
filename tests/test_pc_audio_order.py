"""Actual microphone transport with a delayed synthetic recognition adapter."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("C1", "C2", "D1")
@pytest.mark.scenario("PC-MICROPHONE-COMMIT-BARRIER")
@pytest.mark.parametrize("bound", [None, 0.25])
def test_microphone_marker_waits_for_preceding_append(tmp_path, monkeypatch, bound):
    calls = []
    close_observations = []
    recognizers, late_events = [], []

    class STT:
        def __init__(self, *args):
            self.release = asyncio.Event()
            recognizers.append(self)

        async def start(self):
            pass

        async def events(self):
            await self.release.wait()
            late_events.append("delivered during cleanup")
            yield {"type": "input_audio_buffer.committed", "item_id": "late-during-disconnect"}
            await asyncio.Event().wait()

        async def append(self, pcm):
            calls.append("append_started")
            await asyncio.sleep(0.05)
            calls.append("append_completed")

        async def commit(self):
            calls.append("commit")

        async def close(self):
            close_observations.append(
                (app.state.active["conversation"].mode, app.state.active["audio"] is not None)
            )
            await asyncio.sleep(0.05)  # Keep cleanup suspended while the ASGI socket exits.

    monkeypatch.setattr("reachy_brain.web.app.Transcription", STT)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app) as client, client.websocket_connect("/control", headers=origin) as control:
        control.send_text("test")
        control.receive_json()
        with client.websocket_connect("/audio", headers=origin) as audio:
            audio.send_text("test")
            assert audio.receive_json()["type"] == "audio_ready"
            control.send_json({"type": "mode", "mode": "conversation"})
            assert control.receive_json()["type"] == "stop"
            assert control.receive_json()["mode"] == "conversation"
            audio.send_bytes(bytes(960))
            end = time.time()
            extra = {} if bound is None else {"capture_clock_uncertainty": bound}
            audio.send_json(
                {"type": "commit", "capture_start": end - 1, "capture_end": end, **extra}
            )
            deadline = time.monotonic() + 2
            while len(calls) < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert calls == ["append_started", "append_completed", "commit"]
            assert app.state.active["conversation"].recording_commits[0][2] == {
                "capture_start": end - 1,
                "capture_end": end,
                **extra,
            }
            core = app.state.active["conversation"]
            original_mode = core.set_mode

            async def cleanup_mode(mode):
                if mode == "aware":
                    recognizers[0].release.set()
                    await asyncio.sleep(0)
                await original_mode(mode)

            core.set_mode = cleanup_mode
        assert app.state.active["conversation"].mode == "aware"
        assert app.state.active["stt"] is None
        assert close_observations == [("aware", True)]
        assert app.state.active["audio"] is None
        assert late_events == []
