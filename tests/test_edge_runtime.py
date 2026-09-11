"""Actual local threads with a fake media device; no physical silence claim."""

import time

import pytest

from reachy_brain.robot.runtime import EdgeRuntime


class Media:
    input_rate = 16000

    def __init__(self):
        self.flushes = 0
        self.audio = []
        self.closed = False

    def capture(self):
        return None

    def push_pcm(self, pcm):
        self.audio.append(pcm)

    def flush(self):
        self.flushes += 1

    def hold(self):
        pass

    def close(self):
        self.closed = True


@pytest.mark.features("D2", "D3", "C2", "D5")
@pytest.mark.scenario("EDGE-WATCHDOG-NO-EVENT-LOOP")
def test_watchdog_stops_without_network_loop_and_closes_threads():
    media = Media()
    runtime = EdgeRuntime(media)
    try:
        now = time.monotonic()
        guard = runtime.supervisor
        guard.connect("s", "c", now=now)
        assert guard.authorize(1, acknowledged_stop=0, now=now)
        assert guard.audio("s", "c", 1, 0, bytes(1920), now=now)
        deadline = time.monotonic() + 2
        while not media.audio and time.monotonic() < deadline:
            time.sleep(0.01)
        assert media.audio
        while not guard.guard.latched and time.monotonic() < deadline:
            time.sleep(0.01)
        assert guard.guard.latched and media.flushes >= 2
        assert not guard.audio("s", "c", 1, 1, bytes(1920), now=time.monotonic())
    finally:
        runtime.close()
    assert media.closed and all(not t.is_alive() for t in runtime.threads)


@pytest.mark.features("D2", "D3", "D4", "C2")
@pytest.mark.scenario("EDGE-AUDIO-CONTROLS")
def test_mute_clears_input_and_volume_applies_at_local_submission():
    import numpy as np

    media = Media()
    runtime = EdgeRuntime(media)
    try:
        with runtime.capture_lock:
            runtime.capture.append({"sequence": 0, "samples": np.ones(320)})
        settings = runtime.configure_audio(True, True, 0.25)
        assert settings["input_generation"] == 1 and not runtime.capture
        assert runtime.supervisor.end_silence == 1.2
        with pytest.raises(ValueError):
            runtime.configure_audio(False, False, float("nan"))
        assert runtime.muted and runtime.volume == 0.25
        assert runtime.configure_audio(False, False, 0.25)["input_generation"] == 2
        now = time.monotonic()
        guard = runtime.supervisor
        guard.connect("s", "c", now=now)
        assert guard.authorize(1, acknowledged_stop=0, now=now)
        pcm = np.full(960, 12000, dtype="<i2").tobytes()
        assert guard.audio("s", "c", 1, 0, pcm, now=now)
        deadline = time.monotonic() + 1
        while not media.audio and time.monotonic() < deadline:
            time.sleep(0.005)
        assert np.all(np.frombuffer(media.audio[0], dtype="<i2") == 3000)
        assert runtime.supervisor.end_silence == 0.7
    finally:
        runtime.close()


@pytest.mark.features("C1", "C2", "D2", "D3", "D4")
@pytest.mark.scenario("EDGE-MANUAL-FINISH-ORDER")
def test_manual_finish_is_coalesced_after_prior_audio():
    import queue

    import numpy as np

    pending = queue.Queue()

    class CaptureMedia(Media):
        def capture(self):
            try:
                return pending.get_nowait()
            except queue.Empty:
                return None

    runtime = EdgeRuntime(CaptureMedia())
    try:
        assert not runtime.request_finish()
        runtime.capture_enabled = True
        pending.put(np.zeros(320, dtype=np.float32))
        deadline = time.monotonic() + 1
        while not runtime.capture and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(runtime.capture) == 1
        assert runtime.request_finish() and runtime.request_finish()
        pending.put(np.zeros(320, dtype=np.float32))
        while len(runtime.capture) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        with runtime.capture_lock:
            packets = list(runtime.capture)
        assert len(packets) == 2
        assert not packets[0]["speech_events"]
        (marker,) = packets[1]["speech_events"]
        assert marker["manual"] and marker["type"] == "speech_end"
        assert marker["sequence"] == packets[1]["sequence"] == packets[0]["sequence"] + 1
        assert not runtime.finish_requested
        assert runtime.request_finish()
        runtime.configure_audio(True, False, 0.8)
        assert not runtime.finish_requested and not runtime.request_finish()
    finally:
        runtime.close()
