"""Separate process with one latest-frame slot in each direction; never stores camera history."""

import io
import multiprocessing
import queue
import time
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from PIL import Image

from .detectors import LocalDetectors
from .scene import SceneChange


def newest(channel, value):
    try:
        channel.put_nowait(value)
        return False
    except queue.Full:
        try:
            channel.get_nowait()
        except queue.Empty:
            pass
        try:
            channel.put_nowait(value)
        except queue.Full:
            pass
        return True


def run_worker(incoming, outgoing, stop, models, *, clock=time.time):
    try:
        import cv2

        with ExitStack() as resources:
            detectors = resources.enter_context(LocalDetectors(Path(models)))
            last_objects = last_probe = 0
            objects = []
            hands = []
            previous = None
            scene = SceneChange()
            settled = 0
            source = None
            last_timestamp = -1
            last_captured = -float("inf")
            resets = 0
            while not stop.is_set():
                try:
                    packet = incoming.get(timeout=0.2)
                except queue.Empty:
                    continue
                started = time.perf_counter()
                identity = (packet["source"], packet["generation"])
                if identity != source:
                    if source is not None:
                        resources.close()
                        detectors = resources.enter_context(LocalDetectors(Path(models)))
                        resets += 1
                    source = identity
                    previous = None
                    scene = SceneChange()
                    objects = []
                    hands = []
                    last_objects = last_probe = 0
                    last_timestamp = -1
                    last_captured = -float("inf")
                    settled = packet["captured"] + 0.6
                captured = packet["captured"]
                if clock() - captured > 1 or captured > clock() + 0.1 or captured <= last_captured:
                    continue
                last_captured = captured
                timestamp = max(last_timestamp + 1, round(captured * 1000))
                last_timestamp = timestamp
                with Image.open(io.BytesIO(packet["jpeg"])) as original:
                    if original.width * original.height > 1280 * 1280:
                        continue
                    original.thumbnail((640, 640))
                    rgb = np.asarray(original.convert("RGB"))
                gray = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (160, 120)).astype(
                    np.float32
                )
                moving = False
                if previous is not None:
                    (dx, dy), response = cv2.phaseCorrelate(previous, gray)
                    moving = response > 0.2 and (abs(dx) > 4 or abs(dy) > 3)
                previous = gray
                if moving or packet.get("moving", False):
                    settled = captured + 0.6
                scene_event = scene.update(rgb, captured, moving=captured < settled)
                if captured - last_objects >= 1 / 3:
                    objects = detectors.objects(rgb, timestamp)
                    last_objects = captured
                people = [o for o in objects if o["label"] == "person"]
                if people or hands or captured - last_probe >= 0.5:
                    hands = detectors.hands(rgb, timestamp)
                    last_probe = captured
                else:
                    hands = []
                newest(
                    outgoing,
                    {
                        "source": packet["source"],
                        "generation": packet["generation"],
                        "captured": captured,
                        "sequence": packet.get("sequence"),
                        "uncertainty": packet.get("uncertainty", 0),
                        "objects": objects,
                        "objects_at": last_objects,
                        "hands": hands,
                        "moving": captured < settled,
                        "scene_change": scene_event,
                        "seconds": time.perf_counter() - started,
                        "detector": detectors.version,
                        "tracking_resets": resets,
                        "support_jpeg": packet["jpeg"],
                    },
                )
    except Exception as exc:
        code = type(exc).__name__
        if isinstance(exc, FileNotFoundError):
            code = "perception_models_missing"
        elif isinstance(exc, ModuleNotFoundError):
            code = "perception_dependency_missing"
        elif isinstance(exc, ValueError) and str(exc) in {
            "model_size_mismatch",
            "model_hash_mismatch",
        }:
            code = str(exc)
        newest(outgoing, {"error": code})


class PerceptionWorker:
    def __init__(self, models):
        context = multiprocessing.get_context("spawn")
        self.incoming = context.Queue(maxsize=1)
        self.outgoing = context.Queue(maxsize=1)
        self.stop_event = context.Event()
        self.process = context.Process(
            target=run_worker,
            args=(self.incoming, self.outgoing, self.stop_event, str(models)),
            daemon=True,
        )
        self.process.start()
        self.dropped = 0
        self.count = 0
        self.started = time.monotonic()
        self.exit_reported = False
        self.offered = 0
        self.last_offered = None
        self.last_result = None

    def offer(
        self, source, generation, captured, jpeg, *, uncertainty=0, moving=False, sequence=None
    ):
        if len(jpeg) > 1024 * 1024:
            raise ValueError("detector_frame_limit")
        self.offered += 1
        self.last_offered = time.monotonic()
        self.dropped += newest(
            self.incoming,
            {
                "source": source,
                "generation": generation,
                "captured": captured,
                "jpeg": jpeg,
                "uncertainty": uncertainty,
                "sequence": sequence,
                "moving": moving,
            },
        )

    def poll(self):
        try:
            result = self.outgoing.get_nowait()
            if "error" in result:
                self.exit_reported = True
                return result
            self.count += 1
            self.last_result = time.monotonic()
            result["effective_fps"] = self.count / max(0.001, time.monotonic() - self.started)
            result["dropped"] = self.dropped
            return result
        except queue.Empty:
            if (
                not self.exit_reported
                and not self.stop_event.is_set()
                and self.process.exitcode is not None
            ):
                self.exit_reported = True
                return {"error": "perception_worker_exited", "exit_code": self.process.exitcode}
            return None

    def health(self):
        now = time.monotonic()
        return {
            "alive": self.process.is_alive(),
            "exit_code": self.process.exitcode,
            "offered_frames": self.offered,
            "received_results": self.count,
            "queue_replacements": self.dropped,
            "seconds_since_offer": None
            if self.last_offered is None
            else max(0, now - self.last_offered),
            "seconds_since_result": None
            if self.last_result is None
            else max(0, now - self.last_result),
        }

    def close(self):
        self.stop_event.set()
        self.process.join(2)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(2)
        for channel in (self.incoming, self.outgoing):
            channel.cancel_join_thread()
            channel.close()
