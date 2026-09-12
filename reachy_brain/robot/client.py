"""PC/backend client for the robot-local edge. Credentials never enter browser/model state."""

import asyncio
import contextlib
import json
import math
import ssl
import time
import uuid
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect

from .protocol import VERSION, validate_ready


class ClockEstimate:
    def __init__(self):
        self.offset = 0
        self.uncertainty = math.inf
        self.updated = 0

    def observe(self, sent, received, remote):
        if (
            not all(type(v) in (int, float) and math.isfinite(v) for v in (sent, received, remote))
            or received < sent
        ):
            raise ValueError("invalid_clock_sample")
        uncertainty = (received - sent) / 2
        offset = remote - (sent + received) / 2
        age = received - self.updated
        incompatible = abs(offset - self.offset) > (
            uncertainty + self.uncertainty + max(0, age) * 0.001
        )
        if uncertainty < self.uncertainty or age > 10 or age < 0 or incompatible:
            self.offset = offset
            self.uncertainty = uncertainty
            self.updated = received

    def local(self, remote, *, now):
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (remote, now)):
            raise ValueError("invalid_clock_mapping")
        age = now - self.updated
        if age < 0:
            self.uncertainty = math.inf
        return {
            "time": remote - self.offset,
            "uncertainty": self.uncertainty + max(0, age) * 0.001,
            "stale": age < 0 or age > 10 or not math.isfinite(self.uncertainty),
        }


class EdgeClient:
    def __init__(self, url, token, *, ca_file=None, on_event=None):
        parsed = urlsplit(url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("invalid_edge_url")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("edge_requires_tls")
        self.url = url.rstrip("/")
        self.token = token
        self.tls = ssl.create_default_context(cafile=ca_file) if parsed.scheme == "https" else None
        self.http = httpx.AsyncClient(
            base_url=self.url,
            headers={"Authorization": "Bearer " + token},
            verify=self.tls or True,
            timeout=3,
        )
        self.clock = ClockEstimate()
        self.session = uuid.uuid4().hex
        self.connection = uuid.uuid4().hex
        self.lock = asyncio.Lock()
        self.control = None
        self.heartbeat_task = None
        self.on_event = on_event
        self.error = None
        self.stop_generation = 0
        self.capabilities = None

    def target(self, path):
        return self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + path

    def hello(self):
        return json.dumps(
            {
                "token": self.token,
                "session": self.session,
                "connection": self.connection,
                "protocol_version": VERSION,
            }
        )

    async def start(self):
        try:
            await self._start()
        except BaseException:
            self.error = "edge_start_failed"
            await self.close()
            raise

    async def _start(self):
        kwargs = {"ssl": self.tls} if self.tls else {}
        self.control = await connect(
            self.target("/control"), max_size=65536, max_queue=8, open_timeout=3, **kwargs
        )
        await self.control.send(self.hello())
        async with asyncio.timeout(3):
            ready = json.loads(await self.control.recv())
        self.capabilities = validate_ready(ready, session=self.session, connection=self.connection)
        self.stop_generation = ready["stop_generation"]
        self.heartbeat_task = asyncio.create_task(self._heartbeat())

    async def command(self, kind, **payload):
        if self.error or not self.control:
            raise RuntimeError("edge_unavailable")
        try:
            async with asyncio.timeout(2):
                async with self.lock:
                    await self.control.send(json.dumps({"type": kind, **payload}))
                    result = json.loads(await self.control.recv())
        except BaseException:
            # No orphan response may be consumed as the reply to a different action.
            self.error = "control_request_interrupted"
            await self.control.close()
            raise
        if "generation" in result:
            self.stop_generation = max(self.stop_generation, result["generation"])
        return result

    async def _heartbeat(self):
        try:
            while True:
                sent = time.time()
                result = await self.command("heartbeat", sent=sent)
                self.clock.observe(sent, time.time(), result["edge_time"])
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
            await self.control.close()
            if self.on_event:
                await self.on_event({"type": "disconnected", "reason": self.error})

    async def microphone(self):
        kwargs = {"ssl": self.tls} if self.tls else {}
        previous = None
        async with connect(
            self.target("/microphone"), max_size=65536, max_queue=8, open_timeout=3, **kwargs
        ) as stream:
            await stream.send(self.hello())
            async for raw in stream:
                packet = json.loads(raw)
                sequence = packet["sequence"]
                packet["gap"] = previous is not None and sequence != previous + 1
                previous = sequence
                packet["mapped_capture"] = self.clock.local(packet["captured"], now=time.time())
                yield packet

    async def snapshot(self, *, preview=False):
        data = bytearray()
        async with self.http.stream(
            "GET", "/frame", params={"preview": str(preview).lower()}
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > 20 * 1024 * 1024:
                    raise RuntimeError("edge_image_limit")
                data.extend(chunk)
            retrieved = float(response.headers["x-edge-retrieved-at"])
            sequence = int(response.headers["x-edge-frame-sequence"])
            moving = response.headers.get("x-edge-moving", "1") != "0"
        return bytes(data), {
            "retrieved": self.clock.local(retrieved, now=time.time()),
            "capture_time_known": False,
            "sequence": sequence,
            "moving": moving,
        }

    async def close(self):
        if self.heartbeat_task and self.heartbeat_task is not asyncio.current_task():
            self.heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.heartbeat_task
        if self.control:
            await self.control.close()
        await self.http.aclose()
