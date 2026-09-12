"""Actual local HTTP controls with unavailable installed workflow files."""

import json

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import CallContext
from reachy_brain.web.app import create_app


@pytest.mark.features("E1", "D6")
@pytest.mark.scenario("WORKFLOW-UNAVAILABLE-STATUS")
@pytest.mark.parametrize("damage", ["missing_manifest", "missing_root", "invalid_manifest"])
def test_unavailable_workflow_preserves_status_without_private_paths(tmp_path, damage):
    root = tmp_path / "private-workflow-location"
    root.mkdir()
    manifest = root / "workflow.json"
    manifest.write_text(json.dumps({"id": "sample", "description": "Synthetic workflow"}))
    config = tmp_path / "installation.json"
    config.write_text(
        json.dumps({"skills": [{"path": str(root), "trusted": True, "enabled": True}]})
    )
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", integration_config=config)
    with TestClient(create_app(settings, token="synthetic-control-token")) as client:
        client.headers["Authorization"] = "Bearer synthetic-control-token"
        assert client.get("/api/integrations").json()["workflows"][0]["available"]
        if damage == "invalid_manifest":
            manifest.write_text("private malformed manifest")
        else:
            manifest.unlink()
            if damage == "missing_root":
                root.rmdir()
        response = client.get("/api/integrations")
        assert response.status_code == 200
        data = response.json()
        assert data["workflows"] == []
        assert data["tools"] and data["connections"]
        assert any(
            d["module"] == "workflows" and d["status"].startswith("invalid_skill_")
            for d in data["diagnostics"]
        )
        assert "private-workflow-location" not in response.text
        assert "private malformed manifest" not in response.text
        denied = client.portal.call(
            client.app.state.executor.execute,
            "workflows__local__load",
            {"id": "sample"},
            CallContext("synthetic", 1),
        )
        assert denied["status"].startswith("invalid_skill_")
        assert "private-workflow-location" not in json.dumps(denied)
        assert "private malformed manifest" not in json.dumps(denied)
        original_config = config.read_bytes()
        for enabled in (False, True):
            setting = client.post(
                "/api/integrations",
                json={"action": "skill_enabled", "skill": "sample", "enabled": enabled},
            )
            assert setting.status_code == 409
            assert setting.json()["error"].startswith("invalid_skill_")
            assert config.read_bytes() == original_config
        # Unrelated tool controls must still work while catalog metadata is unavailable.
        tool = "workflows__local__load"
        setting = client.post(
            "/api/integrations",
            json={"action": "tool_policy", "tool": tool, "policy": "deny"},
        )
        assert setting.status_code == 200
        if not root.exists():
            root.mkdir()
        manifest.write_text(json.dumps({"id": "sample", "description": "Synthetic workflow"}))
        recovered = client.get("/api/integrations").json()
        assert recovered["workflows"][0]["available"]
        assert not any(d["module"] == "workflows" for d in recovered["diagnostics"])
        assert next(t for t in recovered["tools"] if t["key"] == tool)["policy"] == "deny"
        denied_after_recovery = client.portal.call(
            client.app.state.executor.execute,
            tool,
            {"id": "sample"},
            CallContext("synthetic", 2),
        )
        assert denied_after_recovery["status"] == "denied"
