"""Protocol negotiation uses real ASGI routing and synthetic robot media."""

import json

import pytest
from fastapi.testclient import TestClient

from reachy_brain.robot.client import EdgeClient
from reachy_brain.robot.service import create_edge_app
from tests.test_edge_service import Media


@pytest.mark.features("D2", "D3", "D5")
@pytest.mark.scenario("EDGE-PROTOCOL-NEGOTIATION")
@pytest.mark.parametrize("version", [None, 0, 2, True, "1"])
def test_incompatible_hello_never_acquires_owner(version):
    token = "synthetic-edge-handshake-token"
    media = Media()
    app = create_edge_app(token, lambda: media)
    with TestClient(app) as client:
        with client.websocket_connect("/control") as ws:
            hello = {"token": token, "session": "s", "connection": "c"}
            if version is not None:
                hello["protocol_version"] = version
            ws.send_json(hello)
            response = ws.receive()
            assert response["type"] == "websocket.close"
            assert response["code"] == 1002
            assert app.state.edge["owner"] is None
            assert media.modes == ["idle"]


@pytest.mark.features("D2", "D3", "D5")
@pytest.mark.scenario("EDGE-READY-COMPATIBILITY")
@pytest.mark.parametrize("fault", ["version", "session", "rate", "channels", "encoding", "stop"])
async def test_backend_rejects_incompatible_ready_before_heartbeat(monkeypatch, fault):
    client = EdgeClient("http://127.0.0.1:8877", "synthetic-edge-handshake-token")
    ready = {
        "type": "ready",
        "protocol_version": 1,
        "session": client.session,
        "connection": client.connection,
        "stop_generation": 0,
        "capabilities": {
            "microphone": {"encoding": "pcm_s16le", "rate": 16000, "channels": 1},
            "playback": {"encoding": "pcm_s16le", "rate": 24000, "channels": 1},
            "local_stop": True,
        },
    }
    if fault == "version":
        ready["protocol_version"] = 2
    elif fault == "session":
        ready["session"] = "old"
    elif fault == "stop":
        ready["capabilities"]["local_stop"] = False
    else:
        ready["capabilities"]["playback"][fault] = {
            "rate": 48000,
            "channels": 2,
            "encoding": "float32",
        }[fault]

    class Socket:
        closed = False
        sent = []

        async def send(self, value):
            self.sent.append(json.loads(value))

        async def recv(self):
            return json.dumps(ready)

        async def close(self):
            self.closed = True

    socket = Socket()

    async def connect(*args, **kwargs):
        return socket

    monkeypatch.setattr("reachy_brain.robot.client.connect", connect)
    try:
        with pytest.raises(RuntimeError, match="edge_.*"):
            await client.start()
        assert client.heartbeat_task is None
        assert socket.closed and client.http.is_closed
        assert len(socket.sent) == 1
        assert socket.sent[0]["protocol_version"] == 1
    finally:
        await client.close()
