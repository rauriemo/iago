"""Deterministic robot-local supervision, independent of network and provider tasks."""

import pytest

from reachy_brain.robot.supervisor import EdgeSupervisor


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
