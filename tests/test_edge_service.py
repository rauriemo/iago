"""ASGI WebSocket ownership/stop contracts with local threads and a fake device."""

import pytest
from fastapi.testclient import TestClient

from reachy_brain.robot.service import create_edge_app


class Media:
    input_rate = 16000

    def pose(self):
        import numpy as np

        return np.eye(4)

    def target(self, pose):
        self.last_pose = pose

    def frame(self):
        import numpy as np

        return np.zeros((16, 16, 3), dtype=np.uint8)

    def __init__(self):
        self.flushes = 0
        self.closed = False
        self.modes = []

    def capture(self):
        return None

    def push_pcm(self, pcm):
        pass

    def flush(self):
        self.flushes += 1

    def hold(self):
        pass

    def close(self):
        self.closed = True

    def set_camera(self, enabled):
        self.camera_enabled = enabled

    def set_mode(self, mode):
        self.modes.append(mode)


@pytest.mark.features("D2", "D3", "D5", "C2")
@pytest.mark.scenario("EDGE-SERVICE-OWNERSHIP")
def test_edge_auth_owner_modes_and_disconnect():
    media = Media()
    token = "synthetic-edge-token-long-enough"
    app = create_edge_app(token, lambda: media)
    with TestClient(app) as client:
        assert client.get("/status").status_code == 401
        with client.websocket_connect("/control") as ws:
            ws.send_json({"token": token, "session": "s", "connection": "c", "protocol_version": 1})
            assert ws.receive_json()["type"] == "ready"
            with client.websocket_connect("/control") as other:
                other.send_json(
                    {"token": token, "session": "s2", "connection": "c2", "protocol_version": 1}
                )
                assert other.receive()["type"] == "websocket.close"
            ws.send_json({"type": "audio_settings", "muted": True, "patient": True, "volume": 0.4})
            controls = ws.receive_json()
            assert controls["muted"] and controls["patient"] and controls["volume"] == 0.4
            assert controls["input_generation"] == 1
            ws.send_json({"type": "mode", "mode": "conversation"})
            state = ws.receive_json()
            assert state["mode"] == "conversation"
            ws.send_json({"type": "camera", "enabled": False})
            assert ws.receive_json()["enabled"] is False
            status = client.get("/status", headers={"Authorization": "Bearer " + token}).json()
            assert status["mode"] == "conversation" and not status["camera_enabled"]
            assert not media.camera_enabled
            assert (
                client.get("/frame", headers={"Authorization": "Bearer " + token}).status_code
                == 409
            )
            ws.send_json({"type": "camera", "enabled": True})
            assert ws.receive_json()["enabled"] is True
            assert media.camera_enabled
            ws.send_json({"type": "finish_turn"})
            assert not ws.receive_json()["accepted"]  # Muted capture cannot queue a turn.
            ws.send_json({"type": "audio_settings", "muted": False, "patient": True, "volume": 0.4})
            assert not ws.receive_json()["muted"]
            ws.send_json({"type": "finish_turn"})
            assert ws.receive_json()["accepted"]
            ws.send_json(
                {"type": "authorize", "epoch": 1, "acknowledged_stop": state["generation"]}
            )
            assert ws.receive_json()["accepted"]
            ws.send_json({"type": "motion_enabled", "enabled": True})
            assert ws.receive_json()["enabled"]
            ws.send_json(
                {
                    "type": "motion_cue",
                    "cue": "acknowledge",
                    "acknowledged_stop": state["generation"],
                }
            )
            assert ws.receive_json()["accepted"]
            ws.send_json({"type": "stop"})
            assert ws.receive_json()["generation"] > state["generation"]
            ws.send_json(
                {"type": "motion_cue", "cue": "speaking", "acknowledged_stop": state["generation"]}
            )
            assert not ws.receive_json()["accepted"]
        status = client.get("/status", headers={"Authorization": "Bearer " + token}).json()
        assert not status["owned"] and status["mode"] == "idle"
        assert not app.state.edge["runtime"].finish_requested
    assert media.closed and media.modes[-1] == "idle"
