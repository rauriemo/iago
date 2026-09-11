"""Robot-local stop path: no async event loop, MCP, skills or PC RPC dependency."""

import threading
from collections import deque

import numpy as np

from reachy_brain.core.ownership import PlaybackGuard


class EdgeSupervisor:
    def __init__(self, sink):
        self.sink = sink
        self.guard = PlaybackGuard()
        self.lock = threading.RLock()
        self.speaking = False
        self.high = self.low = 0
        self.end_silence = 0.7
        self.events = deque(maxlen=100)

    def connect(self, session, connection, *, now):
        self.stop()
        with self.lock:
            self.guard.connect(session, connection, now=now)
            self.events.clear()

    def stop(self):
        with self.lock:
            generation = self.guard.stop()
            self.sink.flush()
            self.events.append({"type": "stop", "generation": generation})
        self.sink.hold()
        return generation

    def heartbeat(self, session, connection, *, now):
        with self.lock:
            if (session, connection) != (self.guard.session, self.guard.connection):
                return False
            if now >= self.guard.deadline and not self.guard.latched:
                self.stop()
            self.guard.deadline = now + self.guard.lease_seconds
            return True

    def authorize(self, epoch, *, acknowledged_stop, now):
        with self.lock:
            if self.speaking:
                return False
            return self.guard.authorize(epoch, acknowledged_stop=acknowledged_stop, now=now)

    def audio(self, session, connection, epoch, sequence, pcm, *, now):
        if not 0 < len(pcm) <= 1920 or len(pcm) % 2:
            raise ValueError("invalid_audio_packet")
        with self.lock:
            if (session, connection) != (self.guard.session, self.guard.connection):
                return False
            if now >= self.guard.deadline:
                if not self.guard.latched:
                    self.stop()
                return False
            if not self.guard.accept(epoch, sequence, now=now):
                return False
            self.sink.push_pcm(pcm)
            return True

    def tick(self, *, now):
        with self.lock:
            if now >= self.guard.deadline and not self.guard.latched:
                self.stop()

    def microphone(self, samples, rate, *, captured=None, sequence=None):
        events = []
        if rate <= 0 or samples.size == 0:
            return events
        mono = samples.mean(axis=1) if samples.ndim == 2 else samples
        duration = len(mono) / rate
        rms = float(np.sqrt(np.mean(np.square(mono))))
        if rms > 0.018:
            self.high += duration
            self.low = 0
        else:
            self.high = 0
            self.low += duration
        if not self.speaking and self.high >= 0.08:
            self.speaking = True
            self.stop()
            event = {
                "type": "speech_start",
                "sequence": sequence,
                "captured": captured + duration - self.high if captured is not None else None,
                "capture_time_estimated": True,
            }
            self.events.append(event)
            events.append(event)
        if self.speaking and self.low >= self.end_silence:
            self.speaking = False
            event = {
                "type": "speech_end",
                "sequence": sequence,
                "captured": captured + duration if captured is not None else None,
                "capture_time_estimated": True,
            }
            self.events.append(event)
            events.append(event)
        return events
