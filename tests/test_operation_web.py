"""Real HTTP/MCP with synthetic local calendar records; no provider accounts."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import CallContext, Rule
from reachy_brain.web.app import create_app


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-OPERATION-HTTP-CONTROLS")
def test_operation_controls_cancel_and_reconcile_without_redispatch(tmp_path):
    config = json.loads(
        Path("examples/integrations.fake-calendar.json").read_text(encoding="utf-8")
    )
    config["modules"][0]["command"] = sys.executable
    config["modules"][0]["args"][-1] = str(tmp_path / "fake.sqlite")
    installation = tmp_path / "integration.json"
    installation.write_text(json.dumps(config), encoding="utf-8")
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", integration_config=installation),
        token="test",
    )
    with TestClient(app) as client:
        assert client.get("/api/operations").status_code == 401
        assert client.post("/api/operations", json={}).status_code == 401
        client.headers["Authorization"] = "Bearer test"
        assert client.post("/api/operations", content="x" * 1025).status_code == 413
        assert client.post("/api/operations", json=[]).status_code == 400
        assert (
            client.post(
                "/api/operations", json={"action": "cancel_undispatched", "operation_id": "unknown"}
            ).status_code
            == 409
        )
        executor = app.state.executor
        context = CallContext("test", 1, operation_id="pending")
        key = "calendar__synthetic-a__create_event"
        payload = {"title": "Private synthetic title", "start": "2030-01-01"}
        client.portal.call(executor.propose, key, payload, context)
        result = client.post(
            "/api/operations", json={"action": "cancel_undispatched", "operation_id": "pending"}
        )
        assert result.json()["canceled"]
        assert result.json()["operation"]["status"] == "canceled-before-dispatch"
        assert "pending" not in executor.pending
        executor.policy.set(Rule(key, "write", "allow"))
        context = CallContext("test", 1, operation_id="lost")
        assert (
            client.portal.call(executor.execute, key, {**payload, "lose_response": True}, context)[
                "status"
            ]
            == "uncertain"
        )
        assert client.post("/api/operations/cancel", json=[]).status_code == 400
        proposal = client.post("/api/operations/cancel", json={"operation_id": "lost"}).json()[
            "confirmation"
        ]
        assert proposal["account"] == "synthetic-a"
        assert proposal["payload"] == {"operation_id": "lost"}
        assert not any(
            row["tool"].endswith("__request_cancel") and row["status"] == "succeeded"
            for row in executor.operations.recent()
        )
        request = {
            "operation_id": "lost",
            "request_id": proposal["operation_id"],
            "binding": proposal["binding"],
        }
        assert (
            client.post(
                "/api/operations/cancel", json={**request, "operation_id": "pending"}
            ).status_code
            == 409
        )
        result = client.post("/api/operations/cancel", json=request)
        assert result.json()["request"]["result"]["outcome"] == "too_late"
        assert executor.operations.get(proposal["operation_id"])["status"] == "succeeded"
        assert client.post("/api/operations/cancel", json=request).status_code == 409
        result = client.post(
            "/api/operations", json={"action": "cancel_undispatched", "operation_id": "lost"}
        )
        assert not result.json()["canceled"]
        lookup = "calendar__synthetic-a__operation_status"
        executor.policy.set(Rule(lookup, "read", "deny"))
        assert (
            client.post(
                "/api/operations", json={"action": "reconcile", "operation_id": "lost"}
            ).status_code
            == 409
        )
        executor.policy.set(Rule(lookup, "read", "allow"))
        result = client.post(
            "/api/operations", json={"action": "reconcile", "operation_id": "lost"}
        )
        assert result.json()["operation"]["status"] == "succeeded"
        events = client.portal.call(
            executor.execute, "calendar__synthetic-a__list_events", {}, context
        )
        assert len(events["result"]["events"]) == 1
        listing = client.get("/api/operations")
        assert len(listing.json()["operations"]) == 3
        assert "Private synthetic title" not in listing.text
        assert all(
            set(row) == {"id", "tool", "status", "updated", "provider_cancel_available"}
            for row in listing.json()["operations"]
        )
