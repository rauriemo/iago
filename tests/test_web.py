"""Local HTTP contracts; no provider calls or physical device claims."""

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("D1", "D4", "D5")
@pytest.mark.scenario("WEB-LOCAL-AUTH")
def test_local_auth_and_secret_exclusion(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, openai_api_key="SYNTHETIC_PRIVATE_KEY")
    with TestClient(create_app(settings, token="test-token")) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").status_code == 401
        response = client.get("/api/status", headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200
        assert "SYNTHETIC_PRIVATE_KEY" not in response.text
        assert response.json()["settings"]["brain_model"] == "gpt-6-astra"
        assert (
            client.get(
                "/api/status",
                headers={
                    "Authorization": "Bearer test-token",
                    "Origin": "https://untrusted.invalid",
                },
            ).status_code
            == 403
        )
        assert client.get("/", headers={"Host": "untrusted.invalid"}).status_code == 400


@pytest.mark.features("P7", "D4", "C2")
@pytest.mark.scenario("WEB-AWARE-STOP")
def test_aware_does_not_open_providers(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, openai_api_key="")
    app = create_app(settings, token="test-token")
    with TestClient(app) as client:
        with client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as ws:
            ws.send_text("test-token")
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "mode", "mode": "aware"})
            assert ws.receive_json()["type"] == "stop"
            assert ws.receive_json()["mode"] == "aware"
            assert app.state.active["stt"] is None
            ws.send_json({"type": "stop", "generation": 4})
            assert ws.receive_json()["acknowledged_stop"] == 4
