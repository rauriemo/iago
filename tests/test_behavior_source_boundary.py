"""Application-owned behavior admission with synthetic camera events."""

import pytest

from reachy_brain.behavior.engine import Event
from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("P4", "P5", "P8", "V9", "E1")
@pytest.mark.scenario("BEHAVIOR-APPLICATION-SOURCE-BOUNDARY")
@pytest.mark.parametrize("change", ["clear", "off", "remove", "screen", "upload"])
def test_retired_or_non_camera_event_cannot_be_taken(tmp_path, change):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="synthetic")
    visual = app.state.visual
    engine = app.state.integration_events.engine
    engine.mode = "aware"
    engine.rules["wave"].enabled = True
    source = visual.source("synthetic-owner", "camera", "Synthetic")
    event = Event("wave", source.id, "wave_detected", 100, 0.99, generation=source.generation)
    assert engine.offer(event, now=100) == "queued"
    if change == "clear":
        visual.clear(source.id)
    if change == "off":
        visual.clear(source.id, disable=True)
    if change == "remove":
        visual.sources.pop(source.id)
    if change in {"screen", "upload"}:
        source.kind = change
    assert engine.take(now=101) is None
    assert engine.log[-1]["decision"] == "source_invalidated"
    assert not engine.last


@pytest.mark.features("E1", "P5", "P8")
@pytest.mark.scenario("BEHAVIOR-APPLICATION-INTEGRATION-BOUNDARY")
def test_combined_validator_preserves_account_generation_checks(tmp_path):
    from reachy_brain.behavior.engine import Rule
    from reachy_brain.integrations.registry import Connection

    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="synthetic")
    ingress = app.state.integration_events
    connection = Connection("synthetic", "a")
    ingress.registry.add_connection(connection)
    engine = ingress.engine
    engine.mode = "aware"
    engine.rules["external"] = Rule("external", "calendar_changed", enabled=True)
    assert (
        ingress.offer(
            "synthetic",
            "a",
            generation=0,
            event_id="one",
            kind="calendar_changed",
            occurred=100,
            now=100,
            payload={},
        )
        == "queued"
    )
    intent = engine.take(now=100)
    assert intent and engine.recheck(intent, now=100)
    connection.disconnect()
    assert not engine.recheck(intent, now=101)


@pytest.mark.features("P4", "P5", "P8", "V1", "V9", "D6")
@pytest.mark.scenario("BEHAVIOR-BROWSER-OWNER-REPLACEMENT")
def test_old_browser_camera_cannot_trigger_in_replacement_session(tmp_path):
    import time

    from fastapi.testclient import TestClient

    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="synthetic")
    headers = {"Authorization": "Bearer synthetic"}
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app) as client:

        def open_aware(control):
            control.send_text("synthetic")
            assert control.receive_json()["type"] == "ready"
            control.send_json({"type": "mode", "mode": "aware"})
            assert control.receive_json()["type"] == "stop"
            assert control.receive_json()["mode"] == "aware"
            response = client.post("/api/source", headers=headers, json={"kind": "camera"})
            assert response.status_code == 200
            return response.json()

        with client.websocket_connect("/control", headers=origin) as control:
            old = open_aware(control)
        with client.websocket_connect("/control", headers=origin) as control:
            fresh = open_aware(control)
            assert old["owner"] != fresh["owner"] and old["id"] != fresh["id"]
            assert old["id"] not in app.state.visual.sources

            def check():
                engine = app.state.integration_events.engine
                engine.mode = "aware"
                engine.rules["wave"].enabled = True
                now = time.time()
                retired = Event(
                    "retired", old["id"], "wave_detected", now, 0.99, generation=old["generation"]
                )
                assert engine.offer(retired, now=now) == "source_invalidated"
                assert engine.take(now=now) is None
                assert not engine.last
                current = Event(
                    "current",
                    fresh["id"],
                    "wave_detected",
                    now,
                    0.99,
                    generation=fresh["generation"],
                )
                assert engine.offer(current, now=now) == "queued"
                assert engine.take(now=now)["evidence"]["source"] == fresh["id"]

            client.portal.call(check)
