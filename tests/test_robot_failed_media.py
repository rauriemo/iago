"""Synthetic native failures retire media before heartbeat-driven teardown."""

import threading
import time
from collections import deque
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from reachy_brain.robot.local_client import LocalClient
from reachy_brain.robot.service import create_edge_app


@pytest.mark.features("D2", "D3", "D5", "V1")
@pytest.mark.scenario("ROBOT-FAILED-RUNTIME-CAMERA")
@pytest.mark.parametrize("transport", ["local", "http"])
@pytest.mark.parametrize("when", ["before", "during"])
async def test_failed_camera_snapshot_cannot_be_delivered(transport, when):
    calls = []
    runtime = SimpleNamespace(
        error="OSError" if when == "before" else None, motion=SimpleNamespace(settle_until=0)
    )

    def snapshot(preview):
        calls.append(preview)
        runtime.error = "OSError"
        return b"synthetic", {"retrieved": time.time(), "sequence": 1, "generation": 0}

    runtime.camera = SimpleNamespace(snapshot=snapshot)
    state = {"runtime": runtime, "mode": "aware", "camera_enabled": True, "owner": ("s", "c")}
    if transport == "local":
        client = LocalClient()
        client.state.update(state)
        with pytest.raises(RuntimeError, match="local_camera"):
            await client.snapshot()
    else:
        app = create_edge_app("x" * 24)
        app.state.edge.update(state)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/frame", headers={"Authorization": "Bearer " + "x" * 24})
        assert response.status_code in {409, 503}
        assert response.content != b"synthetic"
    assert len(calls) == (0 if when == "before" else 1)


@pytest.mark.features("C1", "D3", "D5")
@pytest.mark.scenario("ROBOT-FAILED-RUNTIME-MICROPHONE")
async def test_local_failure_does_not_drain_buffered_microphone():
    client = LocalClient()
    packets = deque([{"sequence": 0, "captured": time.time(), "samples": np.zeros(320)}])
    client.state.update(
        owner=("s", "c"),
        runtime=SimpleNamespace(error="OSError", capture=packets, capture_lock=threading.Lock()),
    )
    stream = client.microphone()
    try:
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert len(packets) == 1 and client.state["microphone"] is None
    finally:
        await stream.aclose()
