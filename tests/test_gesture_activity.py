"""Synthetic diagnostic observations do not qualify live gesture accuracy."""

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.core.gesture_activity import GestureActivity
from reachy_brain.web.app import create_app


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("GESTURE-ACTIVITY-BOUNDS")
def test_observations_are_bounded_and_snapshot_is_detached():
    activity = GestureActivity(clock=lambda: 10.0)
    for n in range(520):
        activity.superseded(str(n), n)
    result = activity.snapshot()
    assert result["counts"]["superseded"] == 520
    assert len(result["samples"]) == 512 and result["dropped_samples"] == 8
    result["samples"][-1]["slot"] = "changed"
    assert activity.snapshot()["samples"][-1]["slot"] == "519"
    activity.accepted({"question": "x" * 1000, "text": "private text"}, 521)
    row = activity.snapshot()["samples"][-1]
    assert row["incomplete"] and "question" not in row and "text" not in row


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("GESTURE-ACTIVITY-AUTHENTICATED-EXPORT")
def test_gesture_export_requires_auth_and_returns_current_owner(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = {"Authorization": "Bearer test", "Origin": "http://127.0.0.1:8765"}
        url = "/api/gesture-activity"
        assert client.get(url).status_code == 401
        assert client.get(url, headers=headers).json() == {"available": False}
        with client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as control:
            control.send_text("test")
            assert control.receive_json()["type"] == "ready"
            activity = app.state.active["conversation"].gesture_activity
            activity.superseded("synthetic-slot", 1)
            response = client.get(url, headers=headers)
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["owner"] == activity.owner
            assert response.json()["counts"]["superseded"] == 1
