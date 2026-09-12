"""Deterministic robot-local supervision, independent of network and provider tasks."""

import pytest

from reachy_brain.robot.supervisor import EdgeSupervisor


@pytest.mark.features("D2", "D3", "C2")
@pytest.mark.scenario("EDGE-EPOCH-QUEUE-ISOLATION")
def test_fresh_epoch_flushes_runtime_and_device_buffers_and_fails_closed():
    import queue
    from collections import deque
    from types import SimpleNamespace

    from reachy_brain.robot.runtime import EdgeRuntime

    pending = []
    fail_flush = False

    def flush():
        if fail_flush:
            raise RuntimeError("device_flush_failed")
        pending.clear()

    # Actual runtime admission/flush with a controlled device, no physical threads.
    runtime = EdgeRuntime.__new__(EdgeRuntime)
    runtime.playback = queue.Queue(maxsize=50)
    runtime.played = deque(maxlen=100)
    runtime.media = SimpleNamespace(flush=flush, hold=lambda: None)
    runtime.motion = SimpleNamespace(stop=lambda **kwargs: None)
    edge = runtime.supervisor = EdgeSupervisor(runtime)
    edge.connect("s", "c", now=1)
    assert edge.authorize(1, acknowledged_stop=0, now=1)
    assert edge.audio("s", "c", 1, 0, bytes(960), now=1.1)
    pending.append(b"previous device submission")
    runtime.played.append({"epoch": 1, "sequence": 0})
    assert not edge.authorize(2, acknowledged_stop=99, now=1.2)
    assert runtime.playback.qsize() == 1 and pending and runtime.played
    assert edge.authorize(2, acknowledged_stop=0, now=1.2)
    assert runtime.playback.empty() and not pending and not runtime.played
    assert not edge.audio("s", "c", 1, 1, bytes(960), now=1.3)
    assert edge.audio("s", "c", 2, 0, bytes(960), now=1.3)
    fail_flush = True
    with pytest.raises(RuntimeError, match="device_flush_failed"):
        edge.authorize(3, acknowledged_stop=0, now=1.4)
    assert edge.guard.latched and edge.guard.stop_generation == 1
    assert not edge.audio("s", "c", 3, 0, bytes(960), now=1.5)
    fail_flush = False
    assert not edge.authorize(4, acknowledged_stop=0, now=1.5)
    assert edge.authorize(4, acknowledged_stop=1, now=1.5)
    assert edge.audio("s", "c", 4, 0, bytes(960), now=1.6)


class Sink:
    def __init__(self):
        self.flushes = 0
        self.audio = []
        self.motion_stops = 0

    def flush(self):
        self.flushes += 1

    def hold(self):
        self.motion_stops += 1

    def push_pcm(self, pcm):
        self.audio.append(pcm)


@pytest.mark.features("D2", "D3", "C2", "P9")
@pytest.mark.scenario("EDGE-STOP-INDEPENDENT-MOTION-CLEANUP")
@pytest.mark.parametrize("fault", ["flush", "hold", "both"])
def test_local_stop_attempts_audio_and_motion_cleanup_independently(fault):
    sink = Sink()
    edge = EdgeSupervisor(sink)
    edge.connect("s", "c", now=1)
    assert edge.authorize(1, acknowledged_stop=0, now=1)
    calls = []

    def flush():
        calls.append("flush")
        if fault in {"flush", "both"}:
            raise OSError("synthetic flush fault")

    def hold():
        calls.append("hold")
        if fault in {"hold", "both"}:
            raise RuntimeError("synthetic hold fault")

    sink.flush, sink.hold = flush, hold
    with pytest.raises(Exception) as failure:
        edge.stop()
    assert calls == ["flush", "hold"]
    assert edge.guard.latched and edge.guard.stop_generation == 1
    assert not edge.audio("s", "c", 1, 0, bytes(960), now=1.1)
    if fault == "both":
        assert isinstance(failure.value, ExceptionGroup)
        assert [type(exc) for exc in failure.value.exceptions] == [OSError, RuntimeError]
    else:
        assert isinstance(failure.value, OSError if fault == "flush" else RuntimeError)


@pytest.mark.features("D2", "D3", "C2", "P9")
@pytest.mark.scenario("EDGE-LOCAL-LEASE-STOP")
def test_local_stop_and_disconnect_reject_late_packets_without_network():
    sink = Sink()
    edge = EdgeSupervisor(sink)
    edge.connect("session", "connection", now=10)
    assert edge.authorize(1, acknowledged_stop=0, now=10)
    assert edge.audio("session", "connection", 1, 0, bytes(960), now=10.1)
    generation = edge.stop()
    assert generation == 1 and sink.flushes >= 1 and sink.motion_stops >= 1
    assert not edge.audio("session", "connection", 1, 1, bytes(960), now=10.2)
    assert not edge.authorize(2, acknowledged_stop=0, now=10.2)
    assert edge.authorize(2, acknowledged_stop=1, now=10.2)
    assert not edge.audio("old", "connection", 2, 0, bytes(960), now=10.3)
    edge.tick(now=11.01)
    assert edge.guard.latched
    assert len(sink.audio) == 1
    assert not edge.audio("session", "connection", 2, 0, bytes(960), now=11.02)


@pytest.mark.features("D2", "D3", "C2")
@pytest.mark.scenario("EDGE-LOCAL-SPEECH-ONSET")
def test_local_vad_flushes_without_waiting_for_pc():
    import numpy as np

    sink = Sink()
    edge = EdgeSupervisor(sink)
    edge.connect("s", "c", now=1)
    edge.authorize(1, acknowledged_stop=0, now=1)
    for _ in range(5):
        edge.microphone(np.full(320, 0.1, dtype=np.float32), 16000)
    assert edge.guard.latched
    assert sink.flushes >= 1
    generation = edge.guard.stop_generation
    for _ in range(20):
        edge.microphone(np.full(320, 0.1, dtype=np.float32), 16000)
    assert edge.guard.stop_generation == generation
