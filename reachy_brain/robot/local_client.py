"""Standalone robot client: same commands, direct in-process media ownership."""

import asyncio
import base64
import contextlib
import threading
import time
import uuid

import numpy as np
from anyio import CancelScope

from .client import ClockEstimate
from .commands import dispatch
from .media import ReachyLocalMedia
from .protocol import capabilities
from .runtime import EdgeRuntime


async def terminal(task):
    """Retain ownership until native work ends, including waiter cancellation."""
    canceled = False
    with CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                canceled = True
        result = task.result()
    if canceled:
        raise asyncio.CancelledError
    return result


class LocalClient:
    _media_owner = threading.Lock()

    def __init__(self, url=None, token=None, *, ca_file=None, on_event=None, media_factory=None):
        self.media_factory = media_factory or ReachyLocalMedia
        self.on_event = on_event
        self.session, self.connection = uuid.uuid4().hex, uuid.uuid4().hex
        self.clock = ClockEstimate()
        self.lock = asyncio.Lock()
        self.error = None
        self.stop_generation = 0
        self.capabilities = None
        self.heartbeat_task = None
        self.owned = False
        self.closed = False
        self.state = dict(
            runtime=None, owner=None, mode="idle", microphone=None, camera_enabled=True
        )

    async def start(self):
        async with self.lock:
            await self._start()

    async def _start(self):
        if self.closed or self.owned:
            raise RuntimeError("local_client_already_started_or_closed")
        if not self._media_owner.acquire(blocking=False):
            raise RuntimeError("local_media_already_owned")
        self.owned = True

        def construct():
            media = self.media_factory()
            try:
                runtime = EdgeRuntime(media)
            except BaseException:
                media.close()
                raise
            self.state["runtime"] = runtime
            runtime.media.set_mode("idle")
            return runtime

        try:
            runtime = await terminal(asyncio.create_task(asyncio.to_thread(construct)))
            self.state["owner"] = (self.session, self.connection)
            runtime.supervisor.connect(self.session, self.connection, now=time.monotonic())
            self.capabilities = capabilities(runtime.media.input_rate)
            now = time.time()
            self.clock.observe(now, now, now)
            self.heartbeat_task = asyncio.create_task(self._heartbeat())
        except BaseException:
            await self._close_locked()
            raise

    async def command(self, kind, **payload):
        async with self.lock:
            if self.closed or self.error or self.state["owner"] is None:
                raise RuntimeError("local_media_unavailable")
            task = asyncio.create_task(
                dispatch(self.state, self.session, self.connection, {"type": kind, **payload})
            )
            try:
                result = await terminal(task)
            except BaseException:
                self.error = "local_command_interrupted"
                await self._close_locked()
                raise
            if result is None:
                raise ValueError("unknown_robot_command")
            if "generation" in result:
                self.stop_generation = max(self.stop_generation, result["generation"])
            return result

    async def _heartbeat(self):
        try:
            while True:
                result = await self.command("heartbeat", sent=time.time())
                now = time.time()
                self.clock.observe(now, now, now)
                for event in result["events"]:
                    if event.get("type") == "stop":
                        self.stop_generation = max(self.stop_generation, event["generation"])
                    if self.on_event:
                        await self.on_event(event)
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = type(exc).__name__
            if not self.closed:
                await self.close()
            if self.on_event:
                await self.on_event({"type": "disconnected", "reason": self.error})

    async def microphone(self):
        if self.state["owner"] is None or self.state["microphone"] is not None:
            raise RuntimeError("local_microphone_unavailable")
        owner = object()
        self.state["microphone"] = owner
        runtime = self.state["runtime"]
        previous = None
        try:
            while not self.closed and not self.error and not runtime.error:
                with runtime.capture_lock:
                    packet = runtime.capture.popleft() if runtime.capture else None
                if packet is None:
                    await asyncio.sleep(0.01)
                    continue
                samples = packet.pop("samples")
                mono = samples.mean(axis=1) if samples.ndim == 2 else samples
                pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()
                sequence = packet["sequence"]
                yield {
                    **packet,
                    "pcm": base64.b64encode(pcm).decode(),
                    "channels": 1,
                    "gap": previous is not None and sequence != previous + 1,
                    "mapped_capture": {
                        "time": packet["captured"],
                        "uncertainty": 0,
                        "stale": False,
                    },
                }
                previous = sequence
        finally:
            if self.state["microphone"] is owner:
                self.state["microphone"] = None

    async def snapshot(self, *, preview=False):
        runtime = self.state["runtime"]
        if (
            self.closed
            or runtime is None
            or runtime.error
            or self.state["mode"] == "idle"
            or not self.state["camera_enabled"]
        ):
            raise RuntimeError("local_camera_inactive")
        result = await asyncio.to_thread(runtime.camera.snapshot, preview)
        if (
            self.closed
            or runtime.error
            or self.state["mode"] == "idle"
            or not self.state["camera_enabled"]
            or not result
        ):
            raise RuntimeError("local_camera_unavailable")
        data, metadata = result
        retrieved = metadata["retrieved"]
        return data, {
            "retrieved": {"time": retrieved, "uncertainty": 0, "stale": False},
            "capture_time_known": False,
            "sequence": metadata["sequence"],
            "moving": time.monotonic() - max(0, time.time() - retrieved)
            < runtime.motion.settle_until,
        }

    async def _close_locked(self):
        self.closed = True
        self.state["mode"] = "idle"
        if self.heartbeat_task and self.heartbeat_task is not asyncio.current_task():
            self.heartbeat_task.cancel()
        if not self.owned:
            return
        runtime = self.state["runtime"]

        def release():
            if runtime:
                runtime.close()
            self.state["owner"] = None
            self.owned = False
            self._media_owner.release()

        await terminal(asyncio.create_task(asyncio.to_thread(release)))

    async def close(self):
        async with self.lock:
            await self._close_locked()
        if self.heartbeat_task and self.heartbeat_task is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError):
                await self.heartbeat_task
