"""Real in-process supervisor/threads with explicitly synthetic physical media."""

import asyncio
import base64
import threading
import time

import numpy as np
import pytest

from reachy_brain.robot.local_client import LocalClient
from tests.test_edge_transport import Media


@pytest.mark.features("D2", "D3", "D5")
@pytest.mark.scenario("ROBOT-IN-PROCESS-OWNERSHIP")
async def test_local_commands_capture_and_exclusive_media():
    media = Media()
    client = LocalClient(media_factory=lambda: media)
    other = LocalClient(media_factory=Media)
    await client.start()
    try:
        with pytest.raises(RuntimeError, match="already_owned"):
            await other.start()
        assert client.capabilities["local_stop"] is True
        mode = await client.command("mode", mode="conversation")
        authorized = await client.command(
            "authorize", epoch=1, acknowledged_stop=mode["generation"]
        )
        assert authorized["accepted"]
        stopped = await client.command("stop")
        assert stopped["generation"] > mode["generation"]
        stale = await client.command("authorize", epoch=1, acknowledged_stop=mode["generation"])
        assert not stale["accepted"]
        runtime = client.state["runtime"]
        with runtime.capture_lock:
            runtime.capture.append(
                {"sequence": 1, "captured": time.time(), "samples": np.ones((8, 2)) * 0.25}
            )
        stream = client.microphone()
        packet = await anext(stream)
        assert not packet["gap"] and packet["channels"] == 1
        assert len(base64.b64decode(packet["pcm"])) == 16
        assert packet["mapped_capture"]["uncertainty"] == 0
        duplicate = client.microphone()
        with pytest.raises(RuntimeError, match="microphone_unavailable"):
            await anext(duplicate)
        await stream.aclose()
        image, timing = await client.snapshot()
        assert image.startswith(b"\xff\xd8") and not timing["capture_time_known"]
        await client.command("mode", mode="idle")
        with pytest.raises(RuntimeError, match="camera_inactive"):
            await client.snapshot()
    finally:
        await client.close()
    assert media.closed and all(not t.is_alive() for t in runtime.threads)
    await other.start()
    await other.close()


@pytest.mark.features("D3", "D5")
@pytest.mark.scenario("ROBOT-IN-PROCESS-CANCELED-RELEASE")
async def test_canceled_close_retains_owner_until_native_completion():
    entered, release = threading.Event(), threading.Event()

    class HeldMedia(Media):
        def close(self):
            entered.set()
            assert release.wait(5)
            super().close()

    client = LocalClient(media_factory=HeldMedia)
    other = LocalClient(media_factory=Media)
    await client.start()
    task = asyncio.create_task(client.close())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(RuntimeError, match="already_owned"):
            await other.start()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    await other.start()
    await other.close()


@pytest.mark.features("D3", "D5")
@pytest.mark.scenario("ROBOT-IN-PROCESS-CANCELED-START")
async def test_canceled_start_releases_constructed_runtime():
    entered, release = threading.Event(), threading.Event()
    media = Media()

    def factory():
        entered.set()
        assert release.wait(5)
        return media

    client = LocalClient(media_factory=factory)
    other = LocalClient(media_factory=Media)
    task = asyncio.create_task(client.start())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(RuntimeError, match="already_owned"):
            await other.start()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert media.closed
    assert all(not t.is_alive() for t in client.state["runtime"].threads)
    await other.start()
    await other.close()


@pytest.mark.features("D2", "D3", "D5")
@pytest.mark.scenario("ROBOT-SHUTDOWN-INDEPENDENT-CLEANUP")
@pytest.mark.parametrize("fault", ["camera", "supervisor"])
async def test_shutdown_failure_still_closes_other_resources(monkeypatch, fault):
    media = Media()
    client = LocalClient(media_factory=lambda: media)
    other = LocalClient(media_factory=Media)
    await client.start()
    runtime = client.state["runtime"]
    # Stop polling before installing the one-shot shutdown fault.
    client.heartbeat_task.cancel()
    try:
        await client.heartbeat_task
    except asyncio.CancelledError:
        pass
    target = runtime.camera if fault == "camera" else runtime.supervisor
    name = "close" if fault == "camera" else "stop"
    original = getattr(target, name)

    def fail_once():
        original()
        monkeypatch.setattr(target, name, original)
        raise RuntimeError("synthetic_shutdown_fault")

    monkeypatch.setattr(target, name, fail_once)
    try:
        with pytest.raises(RuntimeError, match="synthetic_shutdown_fault"):
            await client.close()
        assert media.closed
        assert runtime.supervisor.guard.latched
        assert all(not t.is_alive() for t in runtime.threads)
        with pytest.raises(RuntimeError, match="already_owned"):
            await other.start()
    finally:
        await client.close()
    await other.start()
    await other.close()


@pytest.mark.features("D3", "D5")
@pytest.mark.scenario("ROBOT-CANCELED-MODE-RELEASE")
async def test_canceled_mode_does_not_leave_capture_running():
    entered, release = threading.Event(), threading.Event()

    class HeldMode(Media):
        def set_mode(self, mode):
            if mode == "conversation":
                entered.set()
                assert release.wait(5)

    media = HeldMode()
    client = LocalClient(media_factory=lambda: media)
    await client.start()
    task = asyncio.create_task(client.command("mode", mode="conversation"))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        runtime = client.state["runtime"]
        assert not runtime.capture_enabled
        assert not runtime.camera.enabled
        assert media.closed
        assert all(not t.is_alive() for t in runtime.threads)
    finally:
        release.set()
        await client.close()


@pytest.mark.features("D3", "D5")
@pytest.mark.scenario("ROBOT-CONCURRENT-START-CLOSE")
async def test_close_waits_for_native_start_before_releasing_claim():
    entered, release = threading.Event(), threading.Event()
    media = Media()

    def factory():
        entered.set()
        assert release.wait(5)
        return media

    client = LocalClient(media_factory=factory)
    other = LocalClient(media_factory=Media)
    start = asyncio.create_task(client.start())
    close = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        close = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already_owned"):
            await other.start()
        release.set()
        await start
        await close
        assert media.closed and client.closed and not client.owned
        assert not client.heartbeat_task or client.heartbeat_task.done()
    finally:
        release.set()
        await asyncio.gather(start, *([close] if close else []), return_exceptions=True)
        await client.close()
        await other.close()


@pytest.mark.features("D3", "D5")
@pytest.mark.scenario("ROBOT-NATIVE-CAPTURE-FAILURE")
async def test_native_capture_failure_releases_and_notifies():
    class FailedCapture(Media):
        def capture(self):
            raise OSError("synthetic_private_device_detail")

    observed = []
    disconnected = asyncio.Event()

    async def event(value):
        observed.append(value)
        if value["type"] == "disconnected":
            disconnected.set()

    media = FailedCapture()
    client = LocalClient(media_factory=lambda: media, on_event=event)
    await client.start()
    try:
        await client.command("mode", mode="conversation")
        await asyncio.wait_for(disconnected.wait(), 2)
        assert media.closed and client.closed and not client.owned
        assert client.state["runtime"].error == "OSError"
        assert "synthetic_private_device_detail" not in str(observed)
        assert all(not t.is_alive() for t in client.state["runtime"].threads)
    finally:
        await client.close()


@pytest.mark.features("D2", "D5")
@pytest.mark.scenario("EDGE-NATIVE-CAPTURE-FAILURE")
def test_network_owner_released_after_native_capture_failure():
    from fastapi.testclient import TestClient

    from reachy_brain.robot.protocol import VERSION
    from reachy_brain.robot.service import create_edge_app

    failed = threading.Event()

    class FailedCapture(Media):
        def capture(self):
            failed.set()
            raise OSError("synthetic_private_device_detail")

    media = FailedCapture()
    token = "synthetic-edge-native-failure-token"
    app = create_edge_app(token, lambda: media)
    with TestClient(app) as client:
        with client.websocket_connect("/control") as ws:
            ws.send_json(
                {"token": token, "session": "s", "connection": "c", "protocol_version": VERSION}
            )
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "mode", "mode": "conversation"})
            assert ws.receive_json()["mode"] == "conversation"
            assert failed.wait(2)
            # Wait for the runtime thread to finish publishing its error.
            runtime = app.state.edge["runtime"]
            runtime.threads[1].join(2)
            assert runtime.error == "OSError"
            ws.send_json({"type": "heartbeat"})

            async def released():
                done = asyncio.Event()
                loop = asyncio.get_running_loop()
                handle = None

                def observe():
                    nonlocal handle
                    if app.state.edge["owner"] is None:
                        done.set()
                    else:
                        handle = loop.call_later(0.01, observe)

                observe()
                try:
                    await asyncio.wait_for(done.wait(), 2)
                finally:
                    if handle:
                        handle.cancel()

            client.portal.call(released)
        assert app.state.edge["owner"] is None
        assert app.state.edge["mode"] == "idle"
        assert not runtime.capture_enabled
    assert media.closed
