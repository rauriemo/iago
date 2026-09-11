"""Authenticated persistent permissions using the test-only direct adapter."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import CallContext
from reachy_brain.web.app import create_app


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-PERSISTED-PROBE-PERMISSION")
def test_saved_deny_precedes_mcp_identity_probe(tmp_path, monkeypatch):
    from reachy_brain.integrations.mcp_adapter import Client

    calls = []
    original = Client.call_tool

    async def observed(client, name, *args, **kwargs):
        calls.append(name)
        return await original(client, name, *args, **kwargs)

    monkeypatch.setattr(Client, "call_tool", observed)
    config = json.loads(
        Path("examples/integrations.fake-calendar.json").read_text(encoding="utf-8")
    )
    config["modules"][0]["command"] = sys.executable
    config["modules"][0]["args"][-1] = str(tmp_path / "fake.sqlite")
    path = tmp_path / "installation.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", integration_config=path)
    with TestClient(create_app(settings, token="test")) as client:
        assert calls == ["connection_identity"]
        client.headers["Authorization"] = "Bearer test"
        assert (
            client.post(
                "/api/integrations",
                json={
                    "action": "tool_policy",
                    "tool": "calendar__synthetic-a__connection_identity",
                    "policy": "deny",
                },
            ).status_code
            == 200
        )
    calls.clear()
    with TestClient(create_app(settings, token="test")) as client:
        assert not calls
        assert not client.app.state.executor.registry.connections[
            ("calendar", "synthetic-a")
        ].identity


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-PERSISTENT-TOOL-PERMISSIONS")
def test_standing_authorization_restart_isolation_and_installation_change(tmp_path):
    config = json.loads(Path("examples/integrations.fake-direct.json").read_text(encoding="utf-8"))
    second = {**config["modules"][0], "account": "synthetic-b"}
    config["modules"].append(second)
    path = tmp_path / "integration.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", integration_config=path)
    key = "counter__synthetic-a__set_counter"
    read = "counter__synthetic-a__read_counter"

    def start():
        return TestClient(create_app(settings, token="test"))

    with start() as client:
        assert (
            client.post(
                "/api/integrations", json={"action": "tool_policy", "tool": key, "policy": "allow"}
            ).status_code
            == 401
        )
        client.headers["Authorization"] = "Bearer test"
        assert client.post("/api/integrations", content="x" * 2049).status_code == 413
        assert (
            client.post(
                "/api/integrations", json={"action": "tool_policy", "tool": key, "policy": "other"}
            ).status_code
            == 400
        )
        executor = client.app.state.executor
        proposal = client.portal.call(
            executor.propose, key, {"value": 1}, CallContext("s", 1, operation_id="old")
        )
        assert proposal["operation_id"] in executor.pending
        assert (
            client.post(
                "/api/integrations", json={"action": "tool_policy", "tool": key, "policy": "allow"}
            ).status_code
            == 200
        )
        assert not executor.pending
        assert (
            client.post(
                "/api/integrations", json={"action": "tool_enabled", "tool": read, "enabled": False}
            ).status_code
            == 200
        )
    with start() as client:
        client.headers["Authorization"] = "Bearer test"
        executor = client.app.state.executor
        result = client.portal.call(
            executor.execute, key, {"value": 2}, CallContext("s", 1, operation_id="new")
        )
        assert (
            result["status"] == "ok"
        )  # No repeated confirmation after valid standing authorization.
        result = client.portal.call(
            executor.execute,
            "counter__synthetic-b__set_counter",
            {"value": 2},
            CallContext("s", 1, operation_id="other-account"),
        )
        assert result["status"] == "confirmation_required"
        assert (
            client.portal.call(executor.execute, read, {}, CallContext("s", 1))["status"]
            == "disabled"
        )
    config["modules"][0]["options"] = {"installation_revision": 2}
    path.write_text(json.dumps(config), encoding="utf-8")
    with start() as client:
        executor = client.app.state.executor
        assert executor.policy.rules[key].mode == "confirm"
        assert executor.registry.tools[read].enabled
