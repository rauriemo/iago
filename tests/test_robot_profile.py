"""Real app/edge transport composition with fake robot media; no provider or device pass."""

import io
import socket
import threading
import time
from unittest.mock import AsyncMock

import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image

from reachy_brain.config import Settings
from reachy_brain.robot.service import create_edge_app
from reachy_brain.web.app import create_app


class Media:
    input_rate = 16000

    def frame(self):
        import numpy as np

        return np.zeros((16, 16, 3), dtype=np.uint8)

    def capture(self):
        return None

    def push_pcm(self, pcm):
        pass

    def flush(self):
        pass

    def hold(self):
        pass

    def close(self):
        pass

    def set_camera(self, enabled):
        self.camera_enabled = enabled

    def set_mode(self, mode):
        pass

    def snapshot(self):
        out = io.BytesIO()
        Image.new("RGB", (32, 32), "black").save(out, format="JPEG")
        return out.getvalue()


@pytest.mark.features("D2", "D3", "D4", "V9")
@pytest.mark.scenario("ROBOT-PAGE-INDEPENDENT-OWNER")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
@pytest.mark.parametrize("transport", ["edge", "webrtc"])
@pytest.mark.parametrize("disable_camera", [False, True])
def test_robot_profile_survives_control_page_loss(
    tmp_path, profile, transport, disable_camera, monkeypatch
):
    from tests.test_robot_video_lifecycle import Consumer

    consumers = []

    def video_factory(*args):
        value = Consumer()
        consumers.append(value)
        return value

    monkeypatch.setattr("reachy_brain.robot.video_consumer.create_video_consumer", video_factory)
    monkeypatch.setattr("reachy_brain.robot.local_client.ReachyLocalMedia", Media)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = "synthetic-profile-edge-token-12345"
    server = uvicorn.Server(
        uvicorn.Config(
            create_edge_app(token, Media),
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            timeout_graceful_shutdown=3,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        app = create_app(
            Settings(
                _env_file=None,
                data_dir=tmp_path,
                deployment_mode=profile,
                robot_camera_transport=transport,
                iago_edge_url=f"http://127.0.0.1:{port}",
                iago_edge_token=token,
            ),
            token="page-token",
        )
        with TestClient(app) as client:
            core = app.state.active["conversation"]
            if profile == "reachy_local":
                from reachy_brain.robot.local_client import LocalClient

                assert isinstance(core.send.__self__.edge, LocalClient)
                assert server.config.app.state.edge["owner"] is None
            session = core.session
            core.heard = AsyncMock()
            core.speech_onset = AsyncMock()
            with client.websocket_connect(
                "/control", headers={"Origin": "http://127.0.0.1:8765"}
            ) as ws:
                ws.send_text("page-token")
                assert ws.receive_json()["deployment"] == profile
                assert ws.receive_json()["mode"] == "idle"
                ws.send_json({"type": "mode", "mode": "aware"})
                assert ws.receive_json()["type"] == "stop"
                for _ in range(10):
                    transition = ws.receive_json()
                    if transition["type"] == "state":
                        break
                    assert transition["type"] == "stop"
                assert transition["mode"] == "aware"
                ws.send_json({"type": "heard", "epoch": 1, "segment": "page-claim"})
                ws.send_json({"type": "speech_start", "captured": time.time()})
                ws.send_json({"type": "heartbeat"})
                assert ws.receive_json()["type"] == "heartbeat"
                core.heard.assert_not_called()
                core.speech_onset.assert_not_called()
                screen = client.post(
                    "/api/source",
                    headers={"Authorization": "Bearer page-token"},
                    json={"kind": "screen", "label": "Synthetic screen"},
                ).json()
                if transport == "webrtc":
                    deadline = time.monotonic() + 2
                    while not app.state.visual.frames and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert app.state.visual.frames
                    assert len(consumers) == 1
                    assert all(
                        not frame.capture_time_known for frame in app.state.visual.frames.values()
                    )
                robot_sources = {
                    key: value
                    for key, value in app.state.visual.sources.items()
                    if value.kind == "camera"
                }
                assert robot_sources
                if disable_camera:
                    ws.send_json({"type": "robot_camera", "enabled": False})
                    for _ in range(10):
                        change = ws.receive_json()
                        if change["type"] == "robot_camera":
                            break
                    assert change["enabled"] is False and core.mode == "aware"
                    if transport == "webrtc":
                        consumers[0].stop.assert_awaited_once()
                    assert app.state.visual.sources[screen["id"]].enabled
                    assert not any(
                        s.enabled and s.kind == "camera" for s in app.state.visual.sources.values()
                    )
                ws.send_json(
                    {"type": "audio_settings", "muted": True, "patient": True, "volume": 0.3}
                )
                ws.send_json({"type": "heartbeat"})
                assert ws.receive_json()["type"] == "heartbeat"
            deadline = time.monotonic() + 2
            while app.state.active["control"] is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert core.mode == "aware" and app.state.active["conversation"].session == session
            assert screen["id"] not in app.state.visual.sources
            if not disable_camera:
                for key, value in robot_sources.items():
                    assert app.state.visual.sources.get(key) is value and value.enabled
                if transport == "webrtc":
                    consumers[0].stop.assert_not_awaited()
                    assert len(consumers) == 1
            with client.websocket_connect(
                "/control", headers={"Origin": "http://127.0.0.1:8765"}
            ) as ws:
                ws.send_text("page-token")
                ready = ws.receive_json()
                assert ready["mode"] == "aware"
                assert ready["audio_settings"] == {"muted": True, "patient": True, "volume": 0.3}
                assert ws.receive_json()["mode"] == "aware"
            assert core.session == session
    finally:
        server.should_exit = True
        thread.join(10)
    assert not thread.is_alive()
