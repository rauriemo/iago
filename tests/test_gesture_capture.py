"""Synthetic HTTP streams test bounded capture, not a physical camera run."""

import json

import httpx
import pytest

from reachy_brain.core.gesture_activity import GestureActivity
from reachy_brain.evals.gesture_capture import record


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("GESTURE-CAPTURE-CONTINUITY")
@pytest.mark.parametrize("case", ["valid", "owner", "gap", "unavailable", "oversized", "existing"])
async def test_capture_preserves_only_complete_continuous_samples(tmp_path, case):
    now = [0.0]
    activity = GestureActivity(clock=lambda: now[0])
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/gesture-activity"
        if calls == 2:
            if case == "owner":
                activity.owner = "changed"
            if case == "gap":
                for i in range(513):
                    activity.superseded(str(i), i)
            if case == "unavailable":
                return httpx.Response(200, json={"available": False})
            if case == "oversized":
                return httpx.Response(200, content=b" " * (512 * 1024 + 1))
        return httpx.Response(200, json={"available": True, **activity.snapshot()})

    async def sleep(seconds):
        now[0] += seconds

    output = tmp_path / "capture.jsonl"
    if case == "existing":
        output.write_text("preserve")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        if case == "valid":
            assert (
                await record(
                    client, output, duration=2, interval=1, clock=lambda: now[0], sleep=sleep
                )
                == 3
            )
        else:
            with pytest.raises(FileExistsError if case == "existing" else ValueError):
                await record(
                    client, output, duration=2, interval=1, clock=lambda: now[0], sleep=sleep
                )
    if case == "existing":
        assert output.read_text() == "preserve" and calls == 0
    else:
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        assert rows[-1]["type"] == ("complete_capture" if case == "valid" else "incomplete")
        if case != "valid":
            assert rows[-1]["samples"] == 1
        assert all(not row.get("physical_qualification", False) for row in rows)


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("GESTURE-CAPTURE-APPLICATION-ENDPOINT")
async def test_actual_authenticated_endpoint_capture(tmp_path):
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

    with TestClient(app) as browser:
        with browser.websocket_connect(
            "/control", headers={"Origin": "http://127.0.0.1:8765"}
        ) as control:
            control.send_text("synthetic-token")
            assert control.receive_json()["type"] == "ready"
            activity = app.state.active["conversation"].gesture_activity
            activity.clock = lambda: elapsed[0]
            activity.started = 0.0
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
