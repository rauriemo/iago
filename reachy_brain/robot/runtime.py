"""Robot-local threads own microphone, bounded playback and the stop watchdog."""

import math
import queue
import threading
import time
from collections import deque

import numpy as np

from .camera import CameraFeed
from .motion import MotionCues
from .supervisor import EdgeSupervisor


class EdgeRuntime:
    def __init__(self, media):
        self.media = media
        self.playback = queue.Queue(maxsize=50)
        self.capture = deque(maxlen=25)
        self.capture_lock = threading.Lock()
        self.done = threading.Event()
        self.supervisor = EdgeSupervisor(self)
        self.motion = MotionCues(media, self.supervisor.lock)
        self.capture_enabled = False
        self.muted, self.patient, self.volume = False, False, 0.8
        self.input_generation = 0
        self.finish_requested = False
        self.error = None
        self.played = deque(maxlen=100)
        self.camera = CameraFeed(lambda: self.media.frame())
        self.threads = [
            threading.Thread(target=fn, daemon=True, name=name)
            for name, fn in [
                ("iago-edge-watchdog", self._watchdog),
                ("iago-edge-mic", self._microphone),
                ("iago-edge-playback", self._playback),
                ("iago-edge-motion", self._motion),
            ]
        ]
        for thread in self.threads:
            thread.start()

    def push_pcm(self, pcm):
        guard = self.supervisor.guard
        try:
            self.playback.put_nowait(
                (
                    guard.session,
                    guard.connection,
                    guard.epoch,
                    guard.stop_generation,
                    guard.sequence,
                    pcm,
                )
            )
        except queue.Full:
            self.supervisor.stop()
            raise RuntimeError("edge_playback_overflow") from None

    def configure_audio(self, muted, patient, volume):
        if (
            type(muted) is not bool
            or type(patient) is not bool
            or type(volume) not in {int, float}
            or not math.isfinite(volume)
            or not 0 <= volume <= 1
        ):
            raise ValueError("invalid_audio_settings")
        with self.supervisor.lock:
            if muted != self.muted:
                self.finish_requested = False
                self.input_generation += 1
                self.supervisor.speaking = False
                self.supervisor.high = self.supervisor.low = 0
                with self.capture_lock:
                    self.capture.clear()
            self.muted, self.patient, self.volume = muted, patient, volume
            self.supervisor.end_silence = 1.2 if patient else 0.7
            return {
                "muted": muted,
                "patient": patient,
                "volume": volume,
                "input_generation": self.input_generation,
            }

    def request_finish(self):
        with self.supervisor.lock:
            if not self.capture_enabled or self.muted:
                return False
            self.finish_requested = True
            return True

    def flush(self):
        while True:
            try:
                self.playback.get_nowait()
            except queue.Empty:
                break
        self.played.clear()
        self.media.flush()

    def hold(self):
        self.motion.stop(now=time.monotonic())
        self.media.hold()

    def _worker_failed(self, exc):
        self.error = type(exc).__name__
        self.done.set()
        try:
            self.supervisor.stop()
        except Exception as cleanup:
            # Preserve coarse failure diagnostics without a native worker traceback
            # exposing device/provider response text. Heartbeat triggers recovery.
            self.error += "/" + type(cleanup).__name__

    def _motion(self):
        while not self.done.wait(0.05):
            try:
                self.motion.tick(now=time.monotonic())
            except Exception as exc:
                self._worker_failed(exc)
                return

    def _watchdog(self):
        while not self.done.wait(0.02):
            try:
                self.supervisor.tick(now=time.monotonic())
            except Exception as exc:
                self.error = type(exc).__name__
                self.done.set()

    def _microphone(self):
        sequence = 0
        while not self.done.is_set():
            if not self.capture_enabled:
                self.done.wait(0.01)
                continue
            try:
                capture_generation = self.input_generation
                samples = self.media.capture()
                if samples is None or not samples.size:
                    self.done.wait(0.005)
                    continue
                at = time.time() - len(samples) / self.media.input_rate
                with self.supervisor.lock:
                    if (
                        not self.capture_enabled
                        or self.muted
                        or capture_generation != self.input_generation
                    ):
                        continue
                    speech_events = self.supervisor.microphone(
                        samples, self.media.input_rate, captured=at, sequence=sequence
                    )
                    if self.finish_requested:
                        self.finish_requested = False
                        self.supervisor.speaking = False
                        self.supervisor.high = self.supervisor.low = 0
                        if not any(event["type"] == "speech_end" for event in speech_events):
                            speech_events.append(
                                {
                                    "type": "speech_end",
                                    "sequence": sequence,
                                    "captured": at + len(samples) / self.media.input_rate,
                                    "manual": True,
                                    "capture_time_estimated": True,
                                }
                            )
                    with self.capture_lock:
                        self.capture.append(
                            {
                                "sequence": sequence,
                                "input_generation": self.input_generation,
                                "captured": at,
                                "rate": self.media.input_rate,
                                "samples": samples.copy(),
                                "speech_events": speech_events,
                            }
                        )
                    sequence += 1
            except Exception as exc:
                self._worker_failed(exc)
                return

    def _playback(self):
        while not self.done.is_set():
            try:
                session, connection, epoch, generation, sequence, pcm = self.playback.get(
                    timeout=0.1
                )
            except queue.Empty:
                continue
            try:
                with self.supervisor.lock:
                    guard = self.supervisor.guard
                    if guard.latched or (session, connection, epoch, generation) != (
                        guard.session,
                        guard.connection,
                        guard.epoch,
                        guard.stop_generation,
                    ):
                        continue
                    scaled = (
                        (np.frombuffer(pcm, dtype="<i2").astype(np.float32) * self.volume)
                        .astype("<i2")
                        .tobytes()
                    )
                    self.media.push_pcm(scaled)
                # Pacing prevents the SDK's bounded sink from dropping a long burst.
                if self.done.wait(len(pcm) / 48000):
                    return
                with self.supervisor.lock:
                    if (
                        not guard.latched
                        and guard.epoch == epoch
                        and guard.stop_generation == generation
                    ):
                        self.played.append(
                            {
                                "type": "played",
                                "epoch": epoch,
                                "sequence": sequence,
                                "timing": "paced_submission_not_physical_completion",
                            }
                        )
            except Exception as exc:
                self._worker_failed(exc)
                return

    def close(self):
        self.done.set()
        self.capture_enabled = False
        self.finish_requested = False
        errors = []
        # Stop output before potentially slow camera cleanup. A failed resource must
        # not prevent the other owners from stopping/releasing their resources.
        for release in (self.supervisor.stop, self.camera.close, self.media.close):
            try:
                release()
            except Exception as exc:
                errors.append(exc)
        for thread in self.threads:
            thread.join(2)
        if any(thread.is_alive() for thread in self.threads):
            errors.append(RuntimeError("edge_thread_shutdown_timeout"))
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("edge_shutdown_failed", errors)
