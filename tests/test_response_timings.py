"""Actual conversation stage wiring with synthetic providers; no physical latency claims."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.core.timing import ResponseTimings
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore
from reachy_brain.web.app import create_app


@pytest.mark.features("D6", "C2")
@pytest.mark.scenario("RESPONSE-TIMINGS-CONTROLLER-STAGES")
@pytest.mark.parametrize("cancel", [False, True])
async def test_response_stage_measurements_and_cancellation(tmp_path, cancel):
    now = [0.0]
    entered, release = asyncio.Event(), asyncio.Event()

    class Brain:
        async def stream(self, messages, tools):
            now[0] = 1.0
            entered.set()
            await release.wait()
            yield {"type": "text", "text": "Synthetic private sentence."}

    class Voice:
        async def stream(self, text):
            now[0] = 2.0
            yield bytes(960)

    async def send(event):
        pass

    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {"openai": Voice()},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.timings = ResponseTimings(clock=lambda: now[0])
    core.mode = "conversation"
    await core.user_turn("Synthetic private question", kind="speech")
    await entered.wait()
    if cancel:
        now[0] = 1.5
        await core.stop()
    release.set()
    await core.task
    snapshot = core.timings.snapshot()
    assert len(snapshot["samples"]) == 1
    row = snapshot["samples"][0]
    assert row["origin"] == "speech"
    assert row["status"] == ("canceled" if cancel else "finished")
    if cancel:
        assert "first_model_text" not in row["stages"]
        assert "first_audio_dispatch" not in row["stages"]
    else:
        assert row["stages"] == dict(
            model_start=0.0, first_model_text=1.0, first_audio_dispatch=2.0, ended=2.0
        )
    assert "private" not in json.dumps(snapshot)
    await core.stop()
    await core.executor.close()


@pytest.mark.features("D6")
@pytest.mark.scenario("RESPONSE-TIMINGS-BOUNDS-AND-EPOCHS")
def test_retention_first_stage_and_closed_epoch_isolation():
    now = [0.0]
    timings = ResponseTimings(clock=lambda: now[0])
    for epoch in range(150):
        timings.begin(epoch, "unknown input must not leak")
        now[0] += 1
        timings.mark(epoch, "first_model_text")
        now[0] += 1
        timings.mark(epoch, "first_model_text")
        timings.finish(epoch, "canceled")
        timings.mark(epoch, "first_audio_dispatch")
    data = timings.snapshot()
    assert len(data["samples"]) == 128 and data["samples"][0]["sequence"] == 23
    assert all(r["stages"] == dict(first_model_text=1.0, ended=2.0) for r in data["samples"])
    data["samples"][0]["stages"].clear()
    assert timings.snapshot()["samples"][0]["stages"]
    assert "unknown input" not in json.dumps(data)


@pytest.mark.features("D6")
@pytest.mark.scenario("RESPONSE-TIMINGS-AUTHENTICATED-EXPORT")
def test_export_requires_auth_and_exposes_active_owner_samples(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    with TestClient(app) as client:
        assert client.get("/api/response-timings").status_code == 401
        assert not client.get(
            "/api/response-timings", headers={"Authorization": "Bearer test"}
        ).json()["available"]
        with client.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as control:
            control.send_text("test")
            assert control.receive_json()["type"] == "ready"
            timings = app.state.active["conversation"].timings
            timings.begin(100, "typed")
            timings.finish(100, "finished")
            result = client.get(
                "/api/response-timings", headers={"Authorization": "Bearer test"}
            ).json()
            assert result["available"] and result["samples"][0]["origin"] == "typed"
