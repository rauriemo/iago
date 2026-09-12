"""Synthetic observations and actual local endpoint, no live camera qualification."""

import json

import httpx
import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.evals.behavior_capture import record


@pytest.mark.features("P1", "P3", "P4", "P8", "D6")
@pytest.mark.scenario("BEHAVIOR-CAPTURE-CONTINUITY")
@pytest.mark.parametrize("case", ["valid", "owner", "gap"])
async def test_capture_excludes_configuration_and_rejects_lost_observations(tmp_path, case):
    now = [0.0]
    engine = BehaviorEngine()
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/behaviors"
        if calls == 2:
            if case == "owner":
                engine.log_owner = "other"
            for i in range(201 if case == "gap" else 1):
                engine.offer(Event(str(i), "camera", "wave_detected", 100, 0.99), now=100)
        return httpx.Response(
            200,
            json={
                "configuration": {"prompt": "private configuration text"},
                "observations": engine.observations(),
            },
        )

    async def sleep(seconds):
        now[0] += seconds

    output = tmp_path / "capture.jsonl"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        if case == "valid":
            assert (
                await record(
                    client, output, duration=1, interval=1, clock=lambda: now[0], sleep=sleep
                )
                == 2
            )
        else:
            with pytest.raises(ValueError):
                await record(
                    client, output, duration=1, interval=1, clock=lambda: now[0], sleep=sleep
                )
    text = output.read_text()
    assert "private configuration text" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[-1]["type"] == ("complete_capture" if case == "valid" else "incomplete")


@pytest.mark.features("P4", "D6")
@pytest.mark.scenario("BEHAVIOR-CAPTURE-APPLICATION-ENDPOINT")
async def test_actual_authenticated_behavior_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from reachy_brain.config import Settings
    from reachy_brain.web.app import create_app

    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "runtime"), token="synthetic-token"
    )
    output = tmp_path / "observations.jsonl"
    elapsed = [0.0]

    async def advance(seconds):
        elapsed[0] += seconds

    with TestClient(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": "Bearer synthetic-token"},
        ) as client:
            assert (
                await record(
                    client,
                    output,
                    duration=0.1,
                    interval=1,
                    clock=lambda: elapsed[0],
                    sleep=advance,
                )
                == 2
            )
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[-1]["type"] == "complete_capture"
    assert rows[1]["observation"]["owner"] == rows[2]["observation"]["owner"]
    assert "synthetic-token" not in output.read_text()
