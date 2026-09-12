"""A terminal native fault rejects commands before the next heartbeat."""

import threading
from types import SimpleNamespace

import pytest

from reachy_brain.robot.commands import dispatch


@pytest.mark.features("C2", "D2", "D3", "D5", "P9")
@pytest.mark.scenario("ROBOT-FAILED-RUNTIME-COMMAND-FENCE")
@pytest.mark.parametrize("kind", ["authorize", "audio_settings", "motion_enabled", "finish_turn"])
async def test_native_failure_rejects_work_but_preserves_explicit_stop(kind):
    calls = []

    def called(*args, **kwargs):
        calls.append(kind)
        return True

    runtime = SimpleNamespace(
        error="OSError",
        supervisor=SimpleNamespace(authorize=called, stop=lambda: 7),
        motion=SimpleNamespace(configure=called, enabled=True),
        configure_audio=called,
        request_finish=called,
    )
    state = {"runtime": runtime, "mode": "conversation"}
    message = {
        "type": kind,
        "epoch": 1,
        "acknowledged_stop": 0,
        "muted": False,
        "patient": False,
        "volume": 1.0,
        "enabled": True,
    }
    with pytest.raises(RuntimeError, match="robot_native_runtime_failed"):
        await dispatch(state, "s", "c", message)
    assert calls == []
    assert await dispatch(state, "s", "c", {"type": "stop"}) == {"type": "stop", "generation": 7}


@pytest.mark.features("D2", "D3", "D5")
@pytest.mark.scenario("ROBOT-NATIVE-TRANSITION-FAULT")
@pytest.mark.parametrize("kind", ["camera", "mode"])
async def test_fault_during_native_transition_cannot_enable_capture(kind):
    changes = []
    runtime = SimpleNamespace(
        error=None,
        supervisor=SimpleNamespace(stop=lambda: 1),
        camera=SimpleNamespace(set_enabled=changes.append),
        capture_enabled=False,
        finish_requested=False,
        capture_lock=threading.Lock(),
        capture=[],
    )

    def device_transition(value):
        runtime.error = "OSError"

    runtime.media = SimpleNamespace(set_camera=device_transition, set_mode=device_transition)
    state = {"runtime": runtime, "mode": "aware", "camera_enabled": False}
    message = {"type": kind, "mode": "conversation", "enabled": True}
    with pytest.raises(RuntimeError, match="robot_native_runtime_failed"):
        await dispatch(state, "s", "c", message)
    assert True not in changes and not runtime.capture_enabled
    assert not state["camera_enabled"] and state["mode"] != "conversation"
