"""Authenticated edge control, mic and snapshot channels; start on the robot, not the PC."""

import asyncio
import base64
import contextlib
import secrets
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from .media import ReachyLocalMedia
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
        if state["mode"] == "idle" or not state["camera_enabled"]:
            raise HTTPException(409, "Camera inactive")
        owner = state["owner"]
        result = await asyncio.to_thread(state["runtime"].camera.snapshot, preview)
        if owner != state["owner"] or state["mode"] == "idle" or not state["camera_enabled"]:
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
        if not secrets.compare_digest(str(message.get("token", "")), token):
            await ws.close(code=1008)
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
        await ws.send_json(
            {
                "type": "ready",
                "session": session,
                "connection": connection,
                "stop_generation": 0,
                "edge_time": time.time(),
            }
        )
        try:
            while True:
                m = await ws.receive_json()
                kind = m.get("type")
                if kind == "heartbeat":
                    supervisor.heartbeat(session, connection, now=time.monotonic())
                    events = []
                    while supervisor.events:
                        events.append(supervisor.events.popleft())
                    while runtime.played:
                        events.append(runtime.played.popleft())
                    await ws.send_json(
                        {
                            "type": "heartbeat",
                            "edge_time": time.time(),
                            "echo": m.get("sent"),
                            "events": events,
                        }
                    )
                elif kind == "stop":
                    await ws.send_json({"type": "stop", "generation": supervisor.stop()})
                elif kind == "audio_settings":
                    settings = runtime.configure_audio(m["muted"], m["patient"], m["volume"])
                    await ws.send_json({"type": "audio_settings", **settings})
                elif kind == "finish_turn":
                    accepted = state["mode"] == "conversation" and runtime.request_finish()
                    await ws.send_json({"type": "finish_queued", "accepted": accepted})
                elif kind == "motion_enabled":
                    runtime.motion.configure(m["enabled"])
                    await ws.send_json(
                        {"type": "motion_enabled", "enabled": runtime.motion.enabled}
                    )
                elif kind == "motion_cue":
                    generation = int(m["acknowledged_stop"])

                    def valid_motion(generation=generation):
                        return (
                            state["owner"] == (session, connection)
                            and state["mode"] != "idle"
                            and supervisor.guard.stop_generation == generation
                            and time.monotonic() < supervisor.guard.deadline
                        )

                    accepted = runtime.motion.cue(m["cue"], valid_motion, now=time.monotonic())
                    await ws.send_json({"type": "motion_cue", "accepted": accepted})
                elif kind == "camera" and type(m.get("enabled")) is bool:
                    enabled = m["enabled"]
                    if state["mode"] != "idle" and enabled != state["camera_enabled"]:
                        runtime.camera.set_enabled(False)
                        await asyncio.to_thread(runtime.media.set_camera, enabled)
                        runtime.camera.set_enabled(enabled)
                    state["camera_enabled"] = enabled
                    await ws.send_json({"type": "camera", "enabled": enabled})
                elif kind == "mode" and m.get("mode") in {"idle", "aware", "conversation"}:
                    supervisor.stop()
                    state["mode"] = "idle"
                    runtime.capture_enabled = False
                    runtime.finish_requested = False
                    runtime.camera.set_enabled(False)
                    await asyncio.to_thread(runtime.media.set_mode, m["mode"])
                    if m["mode"] != "idle" and not state["camera_enabled"]:
                        await asyncio.to_thread(runtime.media.set_camera, False)
                    state["mode"] = m["mode"]
                    runtime.camera.set_enabled(m["mode"] != "idle" and state["camera_enabled"])
                    runtime.capture_enabled = m["mode"] == "conversation"
                    with runtime.capture_lock:
                        runtime.capture.clear()
                    await ws.send_json(
                        {
                            "type": "mode",
                            "mode": state["mode"],
                            "generation": supervisor.guard.stop_generation,
                        }
                    )
                elif kind == "authorize":
                    accepted = state["mode"] == "conversation" and supervisor.authorize(
                        int(m["epoch"]),
                        acknowledged_stop=int(m["acknowledged_stop"]),
                        now=time.monotonic(),
                    )
                    await ws.send_json(
                        {"type": "authorized", "accepted": accepted, "epoch": m["epoch"]}
                    )
                elif kind == "audio":
                    pcm = base64.b64decode(m["pcm"], validate=True)
                    accepted = state["mode"] == "conversation" and supervisor.audio(
                        session,
                        connection,
                        int(m["epoch"]),
                        int(m["sequence"]),
                        pcm,
                        now=time.monotonic(),
                    )
                    await ws.send_json(
                        {"type": "queued", "accepted": accepted, "sequence": m["sequence"]}
                    )
        except (WebSocketDisconnect, RuntimeError, ValueError, KeyError):
            pass
        finally:
            supervisor.stop()
            runtime.capture_enabled = False
            runtime.finish_requested = False
            runtime.camera.set_enabled(False)
            state["mode"] = "idle"
            try:
                await asyncio.to_thread(runtime.media.set_mode, "idle")
            finally:
                # A replacement controller cannot acquire media while the old
                # controller's asynchronous release is still in flight.
                state["owner"] = None

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
            while state["owner"] == owner:
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
