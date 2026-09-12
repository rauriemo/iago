"""One raw camera reader with bounded latest-frame storage and independent JPEG views."""

import io
import threading
import time

import numpy as np
from PIL import Image


class CameraFeed:
    def __init__(self, read, *, rate=10):
        self.read = read
        self.interval = 1 / min(10, max(1, rate))
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.enabled = False
        self.generation = 0
        self.sequence = 0
        self.latest = None
        self.encoded = {}
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="iago-edge-camera")
        self.thread.start()

    def set_enabled(self, enabled):
        with self.lock:
            self.enabled = enabled
            self.generation += 1
            self.latest = None
            self.encoded.clear()
            self.ready.clear()

    def _run(self):
        while not self.done.is_set():
            started = time.perf_counter()
            with self.lock:
                enabled, generation = self.enabled, self.generation
            if enabled:
                try:
                    bgr = self.read()
                    retrieved = time.time()
                    if bgr is not None:
                        if (
                            bgr.dtype != np.uint8
                            or bgr.ndim != 3
                            or bgr.shape[2] != 3
                            or bgr.shape[0] * bgr.shape[1] > 20_000_000
                        ):
                            raise ValueError("camera_frame_format")
                        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
                        with self.lock:
                            if self.enabled and self.generation == generation:
                                self.sequence += 1
                                self.latest = (rgb, retrieved, self.sequence, generation)
                                self.encoded.clear()
                                self.ready.set()
                except Exception as exc:
                    self.error = type(exc).__name__
            self.done.wait(max(0, self.interval - (time.perf_counter() - started)))

    def snapshot(self, preview=False):
        self.ready.wait(1)
        with self.lock:
            latest = self.latest
            if not self.enabled or latest is None or not 0 <= time.time() - latest[1] <= 1:
                return None
            cached = self.encoded.get(preview)
            if cached:
                return cached
        rgb, retrieved, sequence, generation = latest
        image = Image.fromarray(rgb)
        if preview:
            image.thumbnail((640, 640))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=80 if preview else 92)
        data = output.getvalue()
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("camera_jpeg_limit")
        result = (
            data,
            {
                "retrieved": retrieved,
                "sequence": sequence,
                "generation": generation,
                "capture_time_known": False,
            },
        )
        with self.lock:
            if (
                not self.enabled
                or self.generation != generation
                or not 0 <= time.time() - retrieved <= 1
            ):
                return None
            if self.latest and self.latest[2] == sequence:
                self.encoded[preview] = result
        return result

    def close(self):
        self.set_enabled(False)
        self.done.set()
        self.thread.join(2)
        if self.thread.is_alive():
            raise RuntimeError("camera_shutdown_timeout")
