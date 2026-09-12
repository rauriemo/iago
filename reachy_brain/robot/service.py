"""Authenticated edge control, mic and snapshot channels; start on the robot, not the PC."""

import asyncio
import base64
import contextlib
import secrets
import time
from contextlib import asynccontextmanager

import numpy as np
from anyio import CancelScope
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .commands import dispatch
from .media import ReachyLocalMedia
from .protocol import VERSION, capabilities, compatible_version
from .runtime import EdgeRuntime


def create_edge_app(token, media_factory=ReachyLocalMedia):
    if len(token) < 24:
        raise ValueError("edge_token_too_short")
    state = {
        "runtime": None,
        "owner": None,
        "mode": "idle",
        "microphone": None,
        "camera_enabled": True,
    }

    @asynccontextmanager
    async def lifespan(app):
        state["runtime"] = await asyncio.to_thread(lambda: EdgeRuntime(media_factory()))
        try:
            await asyncio.to_thread(state["runtime"].media.set_mode, "idle")
            yield
        finally:
            await asyncio.to_thread(state["runtime"].close)

    app = FastAPI(lifespan=lifespan)
    app.state.edge = state

    def auth(request):
        if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "Edge authorization required")

    @app.get("/status")
    async def status(request: Request):
        auth(request)
        runtime = state["runtime"]
        return {
            "mode": state["mode"],
            "owned": state["owner"] is not None,
            "error": runtime.error,
            "input_rate": runtime.media.input_rate,
            "stop_generation": runtime.supervisor.guard.stop_generation,
            "queued_packets": runtime.playback.qsize(),
            "physical_qualification": "pending",
            "camera_enabled": state["camera_enabled"],
        }

    @app.get("/frame")
    async def frame(request: Request, preview: bool = False):
        auth(request)
        runtime = state["runtime"]
        if (
            runtime is None
            or runtime.error
            or state["mode"] == "idle"
            or not state["camera_enabled"]
        ):
            raise HTTPException(409, "Camera inactive")
        owner = state["owner"]
        result = await asyncio.to_thread(runtime.camera.snapshot, preview)
        if (
            runtime.error
            or runtime is not state["runtime"]
            or owner != state["owner"]
            or state["mode"] == "idle"
            or not state["camera_enabled"]
        ):
            raise HTTPException(409, "Camera request canceled")
        if not result:
            raise HTTPException(503, "Camera frame unavailable")
        data, metadata = result
        return Response(
            data,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store",
                "X-Edge-Retrieved-At": str(metadata["retrieved"]),
                "X-Edge-Frame-Sequence": str(metadata["sequence"]),
                "X-Edge-Camera-Generation": str(metadata["generation"]),
                "X-Edge-Moving": "1"
                if time.monotonic() - max(0, time.time() - metadata["retrieved"])
                < state["runtime"].motion.settle_until
                else "0",
                "X-Capture-Timing": "SDK retrieval time; capture time unavailable",
            },
        )

    async def authenticate(ws):
        await ws.accept()
        async with asyncio.timeout(5):
            message = await ws.receive_json()
        if not isinstance(message, dict) or not secrets.compare_digest(
            str(message.get("token", "")), token
        ):
            await ws.close(code=1008)
            return None
        if not compatible_version(message):
            await ws.close(code=1002)
            return None
        return message

    @app.websocket("/control")
    async def control(ws: WebSocket):
        hello = await authenticate(ws)
        if hello is None:
            return
        if state["owner"] is not None:
            await ws.close(code=1008)
            return
        session, connection = hello.get("session"), hello.get("connection")
        if (
            not isinstance(session, str)
            or not isinstance(connection, str)
            or not 1 <= len(session) <= 100
            or not 1 <= len(connection) <= 100
        ):
            await ws.close(code=1008)
            return
        state["owner"] = (session, connection)
        runtime = state["runtime"]
        supervisor = runtime.supervisor
        supervisor.connect(session, connection, now=time.monotonic())
        try:
            await ws.send_json(
                {
                    "type": "ready",
                    "protocol_version": VERSION,
                    "session": session,
                    "connection": connection,
                    "stop_generation": 0,
                    "edge_time": time.time(),
                    "capabilities": capabilities(runtime.media.input_rate),
                }
            )
            while True:
                m = await ws.receive_json()
                result = await dispatch(state, session, connection, m)
                if result is not None:
                    await ws.send_json(result)
        except (WebSocketDisconnect, RuntimeError, ValueError, KeyError):
            pass
        finally:
            supervisor.stop()
            runtime.capture_enabled = False
            runtime.finish_requested = False
            runtime.camera.set_enabled(False)
            state["mode"] = "idle"
            release = asyncio.create_task(asyncio.to_thread(runtime.media.set_mode, "idle"))
            canceled = False
            try:
                with CancelScope(shield=True):
                    while not release.done():
                        try:
                            await asyncio.shield(release)
                        except asyncio.CancelledError:
                            canceled = True
                    release.result()
            except Exception as exc:
                runtime.error = "media_release_failed:" + type(exc).__name__
                raise
            # Native media work cannot be canceled with its asynchronous waiter.
            # Transfer ownership only after observing successful completion.
            state["owner"] = None
            if canceled:
                raise asyncio.CancelledError

    @app.websocket("/microphone")
    async def microphone(ws: WebSocket):
        hello = await authenticate(ws)
        if hello is None:
            return
        owner = (hello.get("session"), hello.get("connection"))
        if state["owner"] != owner:
            await ws.close(code=1008)
            return
        if state["microphone"] is not None:
            await ws.close(code=1008)
            return
        state["microphone"] = ws
        runtime = state["runtime"]
        try:
            while state["owner"] == owner and not runtime.error:
                with runtime.capture_lock:
                    packet = runtime.capture.popleft() if runtime.capture else None
                if packet:
                    samples = packet.pop("samples")
                    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
                    pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()
                    await ws.send_json(
                        {**packet, "pcm": base64.b64encode(pcm).decode(), "channels": 1}
                    )
                else:
                    await asyncio.sleep(0.01)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if state["microphone"] is ws:
                state["microphone"] = None
            with contextlib.suppress(RuntimeError):
                await ws.close()

    return app
