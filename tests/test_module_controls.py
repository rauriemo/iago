"""Module admission/restart controls using an installed synthetic direct adapter."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import CallContext
from reachy_brain.web.app import create_app


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-MODULE-ENABLE-CONTROLS")
def test_module_disable_restart_enable_preserves_policy(tmp_path):
    config = json.loads(Path("examples/integrations.fake-direct.json").read_text(encoding="utf-8"))
    config["modules"][0]["capabilities"] = ["synthetic_counter"]
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    (workflow / "workflow.json").write_text(
        json.dumps(
            {
                "id": "counter-workflow",
                "description": "Synthetic capability",
                "capabilities": ["synthetic_counter"],
            }
        ),
        encoding="utf-8",
    )
    config["skills"] = [{"path": str(workflow), "trusted": True, "enabled": True}]
    path = tmp_path / "installation.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", integration_config=path)
    write = "counter__synthetic-a__set_counter"
    read = "counter__synthetic-a__read_counter"
    action = {"action": "module_enabled", "module": "counter", "account": "synthetic-a"}
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        assert client.get("/api/integrations").json()["workflows"][0]["available"]
        assert (
            client.post(
                "/api/integrations", json={"action": "tool_policy", "tool": write, "policy": "deny"}
            ).status_code
            == 200
        )
        result = client.post("/api/integrations", json={**action, "enabled": False})
        assert result.status_code == 200
        assert not result.json()["workflows"][0]["available"]
        assert result.json()["modules"] == [
            {
                "module": "counter",
                "account": "synthetic-a",
                "enabled": False,
                "active": False,
                "restart_required": False,
            }
        ]
        executor = client.app.state.executor
        assert (
            client.portal.call(executor.execute, read, {}, CallContext("s", 1))["status"]
            == "disabled"
        )
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        assert read not in client.app.state.executor.registry.tools
        # Saving another tool while this module is absent must retain its saved denial.
        assert (
            client.post(
                "/api/integrations",
                json={
                    "action": "tool_policy",
                    "tool": "documents__active__search_project_documents",
                    "policy": "deny",
                },
            ).status_code
            == 200
        )
        result = client.post("/api/integrations", json={**action, "enabled": True})
        assert result.json()["modules"][0]["restart_required"]
        assert read not in client.app.state.executor.registry.tools
        assert (
            client.post(
                "/api/integrations", json={**action, "module": "missing", "enabled": True}
            ).status_code
            == 409
        )
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        executor = client.app.state.executor
        assert executor.policy.rules[write].mode == "deny"
        assert client.portal.call(executor.execute, read, {}, CallContext("s", 1))["status"] == "ok"
        assert client.get("/api/integrations").json()["modules"][0]["active"]
