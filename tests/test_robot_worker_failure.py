"""Synthetic native faults must terminate worker loops without leaking exception text."""

import queue
import threading
from types import SimpleNamespace

import pytest

from reachy_brain.robot.runtime import EdgeRuntime


@pytest.mark.features("C2", "D2", "D3", "D5")
@pytest.mark.scenario("ROBOT-WORKER-FAILURE-TERMINAL")
@pytest.mark.parametrize("worker", ["_microphone", "_motion", "_playback"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_worker_failure_stops_other_workers_and_contains_cleanup_fault(worker, cleanup_failure):
    runtime = EdgeRuntime.__new__(EdgeRuntime)
    runtime.done = threading.Event()
    runtime.error = None
    runtime.capture_enabled = True
    runtime.input_generation = 0
    calls = []

    def failed_work(*args, **kwargs):
        raise OSError("synthetic-private-device-response")

    def stop():
        calls.append(("stop", runtime.done.is_set()))
        # End the old motion loop as well, so a failing test cannot hang.
        runtime.done.set()
        if cleanup_failure:
            raise RuntimeError("synthetic-private-cleanup-response")

    runtime.media = SimpleNamespace(capture=failed_work, push_pcm=failed_work)
    runtime.motion = SimpleNamespace(tick=failed_work)
    runtime.supervisor = SimpleNamespace(
        stop=stop,
        lock=threading.RLock(),
        guard=SimpleNamespace(
            latched=False, session="s", connection="c", epoch=1, stop_generation=0
        ),
    )
    runtime.volume = 1.0
    runtime.playback = queue.Queue()
    runtime.playback.put(("s", "c", 1, 0, 0, bytes(960)))
    getattr(runtime, worker)()
    assert runtime.done.is_set() and calls == [("stop", True)]
    assert runtime.error == ("OSError/RuntimeError" if cleanup_failure else "OSError")
