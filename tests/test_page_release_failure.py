"""Synthetic edge-stop failure must not retain page sources or page ownership."""

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("D2", "D3", "D4", "V9")
@pytest.mark.scenario("PAGE-RELEASE-EDGE-FAILURE")
def test_edge_stop_failure_still_clears_all_page_sources_and_allows_reattachment(
    tmp_path, monkeypatch
):
    class Robot:
        fail_stop = False
        audio_settings = {"muted": False, "patient": False, "volume": 0.8}
        camera_enabled = True

        def __init__(self, settings, gate, core, notify, **kwargs):
            self.notify = notify

        async def start(self):
            pass

        async def close(self):
            pass

        async def send(self, message):
            if self.fail_stop and message["type"] == "stop":
                raise RuntimeError("synthetic_edge_unavailable")
            await self.notify(message)

    monkeypatch.setattr("reachy_brain.web.app.RobotSession", Robot)
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, deployment_mode="reachy_pc"), token="test"
    )
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app) as client:
        core = app.state.active["conversation"]
        core.mode = "aware"
        robot_source = app.state.visual.source(core.connection, "camera", "Robot synthetic")
        with pytest.raises(RuntimeError, match="synthetic_edge_unavailable"):
            with client.websocket_connect("/control", headers=origin) as control:
                control.send_text("test")
                assert control.receive_json()["type"] == "ready"
                control.receive_json()
                for kind in ["screen", "camera"]:
                    assert (
                        client.post(
                            "/api/source",
                            headers={"Authorization": "Bearer test"},
                            json={"kind": kind},
                        ).status_code
                        == 200
                    )
                core.history.append({"role": "assistant", "content": "Synthetic derived context"})
                Robot.fail_stop = True
        Robot.fail_stop = False
        assert app.state.active["control"] is None
        assert not core.history
        assert [s.id for s in app.state.visual.sources.values() if s.enabled] == [robot_source.id]
        with client.websocket_connect("/control", headers=origin) as control:
            control.send_text("test")
            assert control.receive_json()["type"] == "ready"
            assert control.receive_json()["mode"] == "aware"
