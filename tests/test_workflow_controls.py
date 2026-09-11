"""Installed workflow controls; actual app, fake MCP records, no provider billing."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import CallContext
from reachy_brain.web.app import create_app


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-WORKFLOW-ENABLE-CONTROLS")
def test_workflow_toggle_persists_and_cannot_grant_trust(tmp_path):
    config = json.loads(
        Path("examples/integrations.fake-calendar.json").read_text(encoding="utf-8")
    )
    config["modules"][0]["command"] = sys.executable
    config["modules"][0]["args"][-1] = str(tmp_path / "fake.sqlite")
    path = tmp_path / "installation.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", integration_config=path)
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        assert (
            client.post(
                "/api/integrations",
                json={
                    "action": "tool_policy",
                    "tool": "calendar__synthetic-a__create_event",
                    "policy": "deny",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/integrations",
                json={"action": "skill_enabled", "skill": "fake-calendar-review", "enabled": False},
            ).status_code
            == 200
        )
        workflows = client.get("/api/integrations").json()["workflows"]
        assert len(workflows) == 1 and not workflows[0]["enabled"]
        executor = client.app.state.executor
        result = client.portal.call(
            executor.execute,
            "workflows__local__load",
            {"id": "fake-calendar-review"},
            CallContext("s", 1),
        )
        assert result["status"] == "skill_unavailable"
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        assert not client.get("/api/integrations").json()["workflows"][0]["enabled"]
        assert (
            client.app.state.executor.policy.rules["calendar__synthetic-a__create_event"].mode
            == "deny"
        )
        assert (
            client.post(
                "/api/integrations",
                json={"action": "skill_enabled", "skill": "fake-calendar-review", "enabled": True},
            ).status_code
            == 200
        )
        assert client.get("/api/integrations").json()["workflows"][0]["enabled"]
        assert (
            client.post(
                "/api/integrations",
                json={"action": "skill_enabled", "skill": "not-installed", "enabled": True},
            ).status_code
            == 409
        )
    config["skills"][0]["trusted"] = False
    path.write_text(json.dumps(config), encoding="utf-8")
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        assert client.get("/api/integrations").json()["workflows"] == []
        assert (
            client.post(
                "/api/integrations",
                json={"action": "skill_enabled", "skill": "fake-calendar-review", "enabled": True},
            ).status_code
            == 409
        )
