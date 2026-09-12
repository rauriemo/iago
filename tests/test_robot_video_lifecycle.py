"""Synthetic SDK frames; real camera adapter ownership and session selection."""

import asyncio
import io
import threading
from unittest.mock import AsyncMock

import numpy as np
import pytest
from PIL import Image

from reachy_brain.config import Settings
from reachy_brain.robot.video_consumer import VideoCamera


class Consumer:
    def __init__(self, *args):
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.latest = (1, np.zeros((720, 1280, 3), dtype=np.uint8))

    def latest_frame(self):
        return self.latest

    def status(self):
        return {"connected": True}


@pytest.mark.features("D2", "V1", "V5")
@pytest.mark.scenario("ROBOT-WEBRTC-SNAPSHOT-OWNERSHIP")
async def test_shared_camera_views_and_stop_restart():
    instances = []

    def factory(*args):
        value = Consumer()
        instances.append(value)
        return value

    camera = VideoCamera("synthetic", "chosen", factory=factory)
    try:
        full, preview = await asyncio.gather(camera.snapshot(), camera.snapshot(preview=True))
        assert len(instances) == 1
        instances[0].start.assert_awaited_once()
        assert Image.open(io.BytesIO(full[0])).size == (1280, 720)
        assert Image.open(io.BytesIO(preview[0])).size == (640, 360)
        assert full[1]["sequence"] == preview[1]["sequence"] == 1
        assert not full[1]["capture_time_known"]
        assert full[1]["retrieved"]["uncertainty"] == 1.0 and full[1]["moving"]
        camera.seen_at -= 2
        with pytest.raises(RuntimeError, match="stale"):
            await camera.snapshot()
        await camera.stop()
        instances[0].stop.assert_awaited_once()
        await camera.snapshot()
        assert len(instances) == 2
    finally:
        await camera.stop()
    instances[-1].stop.assert_awaited_once()


@pytest.mark.features("D2", "V1", "D5")
@pytest.mark.scenario("ROBOT-WEBRTC-PENDING-STOP")
async def test_stop_invalidates_pending_frame_and_missing_credentials_remain_private():
    consumer = Consumer()
    consumer.latest = None
    camera = VideoCamera("synthetic", "chosen", factory=lambda *args: consumer)
    task = asyncio.create_task(camera.snapshot())
    await asyncio.sleep(0)
    consumer.start.assert_awaited_once()
    await camera.stop()
    with pytest.raises(RuntimeError, match="canceled"):
        await task
    consumer.stop.assert_awaited_once()
    settings = Settings(_env_file=None, robot_camera_hf_token="private-synthetic-hf-token")
    assert "robot_camera_hf_token" not in settings.public()
    assert "private-synthetic-hf-token" not in str(settings.public())
    missing = VideoCamera("", "")
    with pytest.raises(ValueError, match="credentials_required"):
        await missing.snapshot()
    assert missing.consumer is None


@pytest.mark.features("D2", "D5", "V1")
@pytest.mark.scenario("ROBOT-WEBRTC-ENCODING-CANCELLATION")
async def test_canceled_encode_keeps_worker_capacity_and_discards_late_result(monkeypatch):
    camera = VideoCamera("synthetic", "chosen", factory=lambda *args: Consumer())
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    encode = camera._encode

    def held(rgb, preview):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return encode(rgb, preview)

    monkeypatch.setattr(camera, "_encode", held)
    first = asyncio.create_task(camera.snapshot())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert camera.encoding.locked()
        await camera.stop()
        second = asyncio.create_task(camera.snapshot())
        await asyncio.sleep(0)
        assert not second.done() and camera.encoding.locked()
        release.set()
        data, _ = await asyncio.wait_for(second, 2)
        assert Image.open(io.BytesIO(data)).size == (1280, 720)
        assert not camera.encoding.locked()
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await camera.stop()


@pytest.mark.features("V5", "D2", "P8")
@pytest.mark.scenario("ROBOT-ACTIVE-MODE-CAMERA-CONTINUITY")
async def test_active_mode_transition_preserves_camera_until_idle():
    from types import SimpleNamespace

    from reachy_brain.robot.session import RobotSession
    from reachy_brain.vision.store import VisualStore

    visual = VisualStore()
    source = visual.source("synthetic", "camera", "Robot")
    core = SimpleNamespace(mode="conversation", visual=visual, connection="synthetic")

    async def mode(value):
        core.mode = value

    core.set_mode = mode
    edge = SimpleNamespace(command=AsyncMock(return_value={"generation": 1}))
    session = RobotSession(
        Settings(_env_file=None),
        None,
        core,
        AsyncMock(),
        client_factory=lambda *args, **kwargs: edge,
    )
    consumer = Consumer()
    camera = VideoCamera("synthetic", "chosen", factory=lambda *args: consumer)
    session.video = camera
    session.source = source
    # Exercise transition scheduling without running a second synthetic media loop.
    session.spawn_camera = lambda coroutine: coroutine.close()
    try:
        await camera.snapshot()
        session.stt = SimpleNamespace(close=AsyncMock())
        await session._set_mode("aware")
        assert camera.consumer is consumer
        consumer.stop.assert_not_awaited()
        assert session.source is source and source.generation == 0
        await session._set_mode("idle")
        assert camera.consumer is None
        consumer.stop.assert_awaited_once()
    finally:
        await camera.stop()


@pytest.mark.features("V5", "D2", "D5")
@pytest.mark.scenario("ROBOT-WEBRTC-ENCODE-SESSION-FENCE")
@pytest.mark.parametrize("change", ["reconnect", "disconnect"])
async def test_snapshot_rejects_encoded_frame_from_retired_sdk_session(monkeypatch, change):
    consumer = Consumer()
    state = {"connected": True, "session_id": "first"}
    consumer.status = lambda: dict(state)
    camera = VideoCamera("synthetic", "chosen", factory=lambda *args: consumer)
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    encode = camera._encode

    def held(rgb, preview):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return encode(rgb, preview)

    monkeypatch.setattr(camera, "_encode", held)
    task = asyncio.create_task(camera.snapshot())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if change == "reconnect":
            state["session_id"] = "second"
        else:
            state["connected"] = False
        release.set()
        with pytest.raises(RuntimeError, match="camera_session_changed"):
            await asyncio.wait_for(task, 2)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await camera.stop()


@pytest.mark.features("V5", "D2", "D5")
@pytest.mark.scenario("ROBOT-WEBRTC-SNAPSHOT-TIME-ISOLATION")
async def test_concurrent_views_preserve_each_observed_frame_time(monkeypatch):
    consumer = Consumer()
    camera = VideoCamera("synthetic", "chosen", factory=lambda *args: consumer)
    clock = [100.0]
    monkeypatch.setattr("reachy_brain.robot.video_consumer.time.time", lambda: clock[0])
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    encode = camera._encode

    def held(rgb, preview):
        if not preview:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        return encode(rgb, preview)

    monkeypatch.setattr(camera, "_encode", held)
    first = asyncio.create_task(camera.snapshot())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        clock[0] = 100.5
        consumer.latest = (2, consumer.latest[1])
        second = asyncio.create_task(camera.snapshot(preview=True))
        await asyncio.sleep(0)
        assert camera.sequence == 2
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert [(r[1]["sequence"], r[1]["retrieved"]["time"]) for r in results] == [
            (1, 100),
            (2, 100.5),
        ]
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await camera.stop()


@pytest.mark.features("V5", "D2", "D5")
@pytest.mark.scenario("ROBOT-WEBRTC-ENCODE-FRESHNESS")
@pytest.mark.parametrize("end", [99, 102])
async def test_encoding_cannot_return_expired_or_future_observation(monkeypatch, end):
    camera = VideoCamera("synthetic", "chosen", factory=lambda *args: Consumer())
    clock = [100.0]
    monkeypatch.setattr("reachy_brain.robot.video_consumer.time.time", lambda: clock[0])
    encode = camera._encode

    def changed(rgb, preview):
        data = encode(rgb, preview)
        clock[0] = end
        return data

    monkeypatch.setattr(camera, "_encode", changed)
    try:
        with pytest.raises(RuntimeError, match="robot_camera_stale"):
            await camera.snapshot()
    finally:
        await camera.stop()
