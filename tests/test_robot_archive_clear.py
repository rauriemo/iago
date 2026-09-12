"""Real archive loop with synthetic edge media; not robot hardware qualification."""

import asyncio
import io
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from reachy_brain.robot.session import RobotSession
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("D2", "D3", "V1", "V5")
@pytest.mark.scenario("ROBOT-ARCHIVE-CLEAR-IN-FLIGHT")
@pytest.mark.parametrize("phase", ["snapshot", "decode"])
@pytest.mark.parametrize("action", ["clear", "end"])
async def test_robot_archive_discards_stale_work_and_continues(
    tmp_path, monkeypatch, phase, action
):
    visual = VisualStore()
    source = visual.source("synthetic", "camera", "Robot")
    core = SimpleNamespace(mode="aware", visual=visual)
    entered, release, second, added = (asyncio.Event() for _ in range(4))
    decode_release = threading.Event()
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "green").save(data, format="PNG")
    pixels = data.getvalue()
    calls = 0

    async def snapshot():
        nonlocal calls
        calls += 1
        if calls == 1 and phase == "snapshot":
            entered.set()
            await release.wait()
        elif calls > 1:
            second.set()
        return pixels, {"sequence": calls}

    original = visual.prepare
    loop = asyncio.get_running_loop()

    def prepare(image):
        if calls == 1 and phase == "decode":
            loop.call_soon_threadsafe(entered.set)
            assert decode_release.wait(5)
        return original(image)

    original_add = visual.add

    def add(*args, **kwargs):
        frame = original_add(*args, **kwargs)
        added.set()
        return frame

    monkeypatch.setattr(visual, "prepare", prepare)
    monkeypatch.setattr(visual, "add", add)
    session = SimpleNamespace(
        capture_wake=asyncio.Event(),
        core=core,
        source=source,
        edge=SimpleNamespace(snapshot=snapshot),
        notify=AsyncMock(),
    )
    task = asyncio.create_task(RobotSession.camera(session))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        visual.clear(source.id)
        if action == "end":
            core.mode = "idle"
        release.set()
        decode_release.set()
        if action == "clear":
            await asyncio.wait_for(second.wait(), 2)
            await asyncio.wait_for(added.wait(), 2)
            assert all(f.generation == source.generation == 1 for f in visual.frames.values())
            assert not task.done()
        else:
            await asyncio.wait_for(task, 2)
            assert not visual.frames
        session.notify.assert_not_awaited()
    finally:
        release.set()
        decode_release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.features("D2", "D3", "V4")
@pytest.mark.scenario("ROBOT-EXPLICIT-CAPTURE-SINGLE-OWNER")
async def test_explicit_capture_wakes_existing_archive_owner():
    visual = VisualStore()
    source = visual.source("synthetic", "camera", "Robot")
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(data, format="PNG")
    snapshots = asyncio.Queue()
    release = asyncio.Event()
    calls = 0

    async def snapshot():
        nonlocal calls
        calls += 1
        snapshots.put_nowait(calls)
        if calls == 1:
            await release.wait()
        return data.getvalue(), {"sequence": calls}

    core = SimpleNamespace(mode="aware", visual=visual)
    session = SimpleNamespace(
        core=core,
        source=source,
        closed=False,
        camera_enabled=True,
        capture_wake=asyncio.Event(),
        edge=SimpleNamespace(snapshot=snapshot),
        notify=AsyncMock(),
    )
    task = asyncio.create_task(RobotSession.camera(session))
    try:
        assert await asyncio.wait_for(snapshots.get(), 1) == 1
        for _ in range(20):
            assert RobotSession.request_capture(session)
        assert calls == 1, "request must not open a parallel camera path"
        release.set()
        assert await asyncio.wait_for(snapshots.get(), 0.75) == 2
        core.mode = "idle"
        assert not RobotSession.request_capture(session)
        core.mode = "aware"
        session.camera_enabled = False
        assert not RobotSession.request_capture(session)
        session.camera_enabled = True
        source.enabled = False
        assert not RobotSession.request_capture(session)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.features("V5", "D2", "D3")
@pytest.mark.scenario("ROBOT-ARCHIVE-SOURCE-SEQUENCE")
async def test_archive_retains_adapter_sequence_and_ignores_repeat(monkeypatch):
    import time

    frozen = time.time()
    monkeypatch.setattr("reachy_brain.robot.session.time.time", lambda: frozen)
    visual = VisualStore()
    source = visual.source("synthetic", "camera", "Robot")
    core = SimpleNamespace(mode="aware", visual=visual)
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(buffer, "PNG")
    snapshots = asyncio.Queue()
    calls = 0

    async def snapshot():
        nonlocal calls
        calls += 1
        snapshots.put_nowait(calls)
        return buffer.getvalue(), {"sequence": 1 if calls <= 2 else 2}

    stored = asyncio.Queue()
    original = visual.add

    def add(*args, **kwargs):
        frame = original(*args, **kwargs)
        stored.put_nowait(frame)
        return frame

    monkeypatch.setattr(visual, "add", add)
    session = SimpleNamespace(
        core=core,
        source=source,
        capture_wake=asyncio.Event(),
        edge=SimpleNamespace(snapshot=snapshot),
        notify=AsyncMock(),
    )
    task = asyncio.create_task(RobotSession.camera(session))
    try:
        first = await asyncio.wait_for(stored.get(), 2)
        assert first.source_frame_id == "1"
        session.capture_wake.set()
        assert await snapshots.get() == 1
        assert await asyncio.wait_for(snapshots.get(), 2) == 2
        await asyncio.sleep(0)
        assert len(visual.frames) == 1
        session.capture_wake.set()
        try:
            second = await asyncio.wait_for(stored.get(), 2)
        except TimeoutError:
            pytest.fail(
                f"archive stopped={task.done()} calls={calls} errors={session.notify.call_args_list}"
            )
        assert second.source_frame_id == "2"
        assert not first.capture_time_known and not second.capture_time_known
        assert len(visual.frames) == 2
        session.notify.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
