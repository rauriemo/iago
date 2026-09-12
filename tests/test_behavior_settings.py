"""Local settings and policy checks; no synthetic detection is a physical pass."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from reachy_brain.behavior.engine import BehaviorEngine
from reachy_brain.behavior.settings import BehaviorConfiguration
from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("P4", "P5", "P7", "P8")
@pytest.mark.scenario("BEHAVIOR-SETTINGS-PERSISTENCE")
def test_atomic_settings_restart_and_invalid_replacement(tmp_path):
    path = tmp_path / "behaviors.json"
    config = BehaviorConfiguration(path, BehaviorEngine())
    data = config.value.model_dump()
    assert not any(rule["enabled"] for rule in data["rules"])
    data.update(quiet_start="22:00", quiet_end="07:00")
    data["rules"][0]["enabled"] = True
    config.save(data)
    original = path.read_bytes()
    restarted = BehaviorConfiguration(path, BehaviorEngine())
    assert restarted.engine.rules["entry"].enabled
    for hour, quiet in [(23, True), (6, True), (7, False), (12, False)]:
        restarted.tick(datetime(2026, 9, 11, hour).timestamp())
        assert restarted.engine.quiet is quiet
    data["rules"][0]["confidence"] = float("nan")
    with pytest.raises(ValidationError):
        config.save(data)
    assert path.read_bytes() == original
    assert config.engine.rules["entry"].confidence == 0.8
    assert not list(tmp_path.glob(".behavior-*"))


@pytest.mark.features("P4", "P5", "D4")
@pytest.mark.scenario("BEHAVIOR-SETTINGS-HTTP")
def test_authenticated_bounded_configuration_api(tmp_path):
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    ) as client:
        assert client.get("/api/behaviors").status_code == 401
        headers = {"Authorization": "Bearer test"}
        observed = client.get("/api/behaviors", headers=headers).json()["observations"]
        assert observed["total"] == 0 and observed["samples"] == []
        assert observed["dropped_samples"] == 0 and observed["sample_limit"] == 200
        assert (
            client.get("/api/behaviors", headers=headers).json()["observations"]["owner"]
            == observed["owner"]
        )
        data = client.get("/api/behaviors", headers=headers).json()["configuration"]
        data["spontaneous"] = False
        assert client.post("/api/behaviors", headers=headers, json=data).status_code == 200
        data["rules"].append(data["rules"][0])
        assert client.post("/api/behaviors", headers=headers, json=data).status_code == 400
        assert (
            client.post("/api/behaviors", headers=headers, content=b"x" * 131073).status_code == 413
        )
        result = client.get("/api/behaviors", headers=headers).json()["configuration"]
        assert result["spontaneous"] is False and len(result["rules"]) == 4
