"""Real local HTTP/WebSocket flow with synthetic providers; no physical or cloud pass."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import Connection
from reachy_brain.web.app import create_app


@pytest.mark.features("P4", "P5", "P6", "P7", "D4", "E1")
@pytest.mark.scenario("TRIGGER-AWARE-TRANSPORT")
@pytest.mark.parametrize("event_source", ["camera", "integration"])
def test_test_event_starts_recognition_greets_and_times_out(tmp_path, monkeypatch, event_source):
    calls = []

    class Brain:
        def __init__(self, *args):
            pass

        async def stream(self, messages, tools):
            calls.append("brain")
            assert "stt_start" in calls
            marker = "manual-test-event" if event_source == "camera" else "integration-ingress-v1"
            assert any(marker in str(message) for message in messages)
            yield {"type": "text", "text": "Hello there."}

        async def close(self):
            pass

    class Voice(Brain):
        async def stream(self, text):
            calls.append("voice")
            yield bytes(1920)

    class STT(Brain):
        async def start(self):
            calls.append("stt_start")

        async def events(self):
            await asyncio.Event().wait()
            yield {}

        async def close(self):
            calls.append("stt_close")

    monkeypatch.setattr("reachy_brain.web.app.AstraBrain", Brain)
    monkeypatch.setattr("reachy_brain.web.app.OpenAISpeech", Voice)
    monkeypatch.setattr("reachy_brain.web.app.Transcription", STT)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    headers = {"Authorization": "Bearer test"}
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app) as client, client.websocket_connect("/control", headers=origin) as control:
        control.send_text("test")
        assert control.receive_json()["type"] == "ready"
        control.send_json({"type": "mode", "mode": "aware"})
        assert control.receive_json()["type"] == "stop"
        assert control.receive_json()["mode"] == "aware"
        source = client.post("/api/source", headers=headers, json={"kind": "camera"}).json()
        disabled = client.post(
            "/api/behaviors/test", headers=headers, json={"rule": "wave", "source": source["id"]}
        ).json()
        assert disabled["decision"] == "disabled" and not calls
        config = client.get("/api/behaviors", headers=headers).json()["configuration"]
        config["conversation_window"] = 5
        for rule in config["rules"]:
            if rule["id"] == "wave":
                rule["enabled"] = True
                rule["modes"] = ["aware"]
        assert client.post("/api/behaviors", headers=headers, json=config).status_code == 200
        with client.websocket_connect("/audio", headers=origin) as audio:
            audio.send_text("test")
            assert audio.receive_json()["type"] == "audio_ready"
            if event_source == "camera":
                result = client.post(
                    "/api/behaviors/test",
                    headers=headers,
                    json={"rule": "wave", "source": source["id"]},
                ).json()
                assert result["synthetic"] and result["decision"] == "queued"
            else:

                def inject():
                    app.state.executor.registry.add_connection(Connection("calendar", "synthetic"))
                    now = time.time()
                    return app.state.integration_events.offer(
                        "calendar",
                        "synthetic",
                        generation=0,
                        event_id="synthetic-event",
                        kind="wave_detected",
                        occurred=now,
                        now=now,
                        payload={"synthetic": True},
                    )

                assert client.portal.call(inject) == "queued"
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if "voice" in calls and app.state.active["conversation"].mode == "aware":
                    break
                time.sleep(0.02)
            assert calls[:3] == ["stt_start", "brain", "voice"]
            assert "stt_close" in calls
            assert app.state.active["conversation"].mode == "aware"
            assert app.state.active["stt"] is None
            observations = client.get("/api/behaviors", headers=headers).json()["observations"]
            decisions = [row["decision"] for row in observations["samples"]]
            assert "accepted" in decisions
            assert "window_starting" in decisions and "greeting_scheduled" in decisions
            assert "window_timed_out" in decisions
            scheduled = next(
                row for row in observations["samples"] if row["decision"] == "greeting_scheduled"
            )
            admitted = next(row for row in observations["samples"] if row["decision"] == "accepted")
            assert (scheduled["event"], scheduled["source"], scheduled["generation"]) == (
                admitted["event"],
                admitted["source"],
                admitted["generation"],
            )
