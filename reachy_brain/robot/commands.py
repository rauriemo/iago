"""Shared robot commands for network and in-process owners.

The robot-local supervisor remains independent of application tools/providers.
"""

import asyncio
import base64
import time


async def dispatch(state, session, connection, m):
    runtime = state["runtime"]
    supervisor = runtime.supervisor
    kind = m.get("type")

    def require_healthy():
        if runtime.error:
            raise RuntimeError("robot_native_runtime_failed")

    if kind != "stop":
        require_healthy()
    if kind == "heartbeat":
        supervisor.heartbeat(session, connection, now=time.monotonic())
        events = []
        while supervisor.events:
            events.append(supervisor.events.popleft())
        while runtime.played:
            events.append(runtime.played.popleft())
        return {
            "type": "heartbeat",
            "edge_time": time.time(),
            "echo": m.get("sent"),
            "events": events,
        }
    elif kind == "stop":
        return {"type": "stop", "generation": supervisor.stop()}
    elif kind == "audio_settings":
        settings = runtime.configure_audio(m["muted"], m["patient"], m["volume"])
        return {"type": "audio_settings", **settings}
    elif kind == "finish_turn":
        accepted = state["mode"] == "conversation" and runtime.request_finish()
        return {"type": "finish_queued", "accepted": accepted}
    elif kind == "motion_enabled":
        runtime.motion.configure(m["enabled"])
        return {"type": "motion_enabled", "enabled": runtime.motion.enabled}
    elif kind == "motion_cue":
        generation = int(m["acknowledged_stop"])

        def valid_motion(generation=generation):
            return (
                state["owner"] == (session, connection)
                and state["mode"] != "idle"
                and (supervisor.guard.stop_generation == generation)
                and (time.monotonic() < supervisor.guard.deadline)
            )

        accepted = runtime.motion.cue(m["cue"], valid_motion, now=time.monotonic())
        return {"type": "motion_cue", "accepted": accepted}
    elif kind == "camera" and type(m.get("enabled")) is bool:
        enabled = m["enabled"]
        if state["mode"] != "idle" and enabled != state["camera_enabled"]:
            runtime.camera.set_enabled(False)
            await asyncio.to_thread(runtime.media.set_camera, enabled)
            require_healthy()
            runtime.camera.set_enabled(enabled)
        state["camera_enabled"] = enabled
        return {"type": "camera", "enabled": enabled}
    elif kind == "mode" and m.get("mode") in {"idle", "aware", "conversation"}:
        supervisor.stop()
        state["mode"] = "idle"
        runtime.capture_enabled = False
        runtime.finish_requested = False
        runtime.camera.set_enabled(False)
        await asyncio.to_thread(runtime.media.set_mode, m["mode"])
        require_healthy()
        if m["mode"] != "idle" and (not state["camera_enabled"]):
            await asyncio.to_thread(runtime.media.set_camera, False)
            require_healthy()
        state["mode"] = m["mode"]
        runtime.camera.set_enabled(m["mode"] != "idle" and state["camera_enabled"])
        runtime.capture_enabled = m["mode"] == "conversation"
        with runtime.capture_lock:
            runtime.capture.clear()
        return {
            "type": "mode",
            "mode": state["mode"],
            "generation": supervisor.guard.stop_generation,
        }
    elif kind == "authorize":
        accepted = state["mode"] == "conversation" and supervisor.authorize(
            int(m["epoch"]), acknowledged_stop=int(m["acknowledged_stop"]), now=time.monotonic()
        )
        return {"type": "authorized", "accepted": accepted, "epoch": m["epoch"]}
    elif kind == "audio":
        pcm = base64.b64decode(m["pcm"], validate=True)
        accepted = state["mode"] == "conversation" and supervisor.audio(
            session, connection, int(m["epoch"]), int(m["sequence"]), pcm, now=time.monotonic()
        )
        return {"type": "queued", "accepted": accepted, "sequence": m["sequence"]}
