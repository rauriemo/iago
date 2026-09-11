"""Optional real MediaPipe tasks, owned by a single perception worker."""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import httpx
import numpy as np


def verify_asset(path, item):
    if path.stat().st_size != item["bytes"]:
        raise ValueError("model_size_mismatch")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != item["sha256"]:
        raise ValueError("model_hash_mismatch")


def download_asset(path, item):
    """Publish only verified bytes, without replacing an existing installation."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".model-", delete=False) as target:
            temporary = Path(target.name)
            size = 0
            with httpx.stream("GET", item["url"], timeout=60, follow_redirects=True) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > item["bytes"]:
                        raise ValueError("model_size_mismatch")
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        verify_asset(temporary, item)
        try:
            os.link(temporary, path)
        except FileExistsError:
            # A concurrent installer may have published first; never replace its file.
            verify_asset(path, item)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def model_paths(directory, *, download=False):
    manifest = json.loads(Path(__file__).with_name("models.json").read_text())
    paths = {}
    directory.mkdir(parents=True, exist_ok=True)
    for name, item in manifest.items():
        path = directory / item["file"]
        if not path.exists() and download:
            download_asset(path, item)
        if not path.exists():
            raise FileNotFoundError("Run python -m reachy_brain.vision.detectors --download")
        verify_asset(path, item)
        paths[name] = path
    return paths


class LocalDetectors:
    def __init__(self, directory):
        import mediapipe as mp

        self.mp = mp
        paths = model_paths(directory)
        self.labels = paths["labels"].read_text().splitlines()
        vision = mp.tasks.vision
        base = mp.tasks.BaseOptions
        self.object_detector = vision.ObjectDetector.create_from_options(
            vision.ObjectDetectorOptions(
                base_options=base(model_asset_path=str(paths["objects"])),
                running_mode=vision.RunningMode.VIDEO,
                max_results=20,
                score_threshold=0.5,
            )
        )
        try:
            self.gesture_detector = vision.GestureRecognizer.create_from_options(
                vision.GestureRecognizerOptions(
                    base_options=base(model_asset_path=str(paths["hands"])),
                    running_mode=vision.RunningMode.VIDEO,
                    num_hands=4,
                    min_hand_detection_confidence=0.6,
                    min_hand_presence_confidence=0.6,
                    min_tracking_confidence=0.6,
                )
            )
        except BaseException:
            self.object_detector.close()
            raise
        self.last_objects = self.last_hands = -1
        self.version = "mediapipe-" + mp.__version__

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.gesture_detector.close()
        self.object_detector.close()

    def _image(self, rgb):
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or max(rgb.shape[:2]) > 1280:
            raise ValueError("invalid_detector_image")
        return self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))

    def objects(self, rgb, timestamp_ms):
        if timestamp_ms <= self.last_objects:
            raise ValueError("nonmonotonic_timestamp")
        self.last_objects = timestamp_ms
        result = self.object_detector.detect_for_video(self._image(rgb), timestamp_ms)
        rows = []
        height, width = rgb.shape[:2]
        for detection in result.detections:
            category = max(detection.categories, key=lambda c: c.score)
            b = detection.bounding_box
            rows.append(
                {
                    "label": category.category_name,
                    "confidence": category.score,
                    "box": [
                        b.origin_x / width,
                        b.origin_y / height,
                        b.width / width,
                        b.height / height,
                    ],
                }
            )
        return rows

    def hands(self, rgb, timestamp_ms):
        if timestamp_ms <= self.last_hands:
            raise ValueError("nonmonotonic_timestamp")
        self.last_hands = timestamp_ms
        result = self.gesture_detector.recognize_for_video(self._image(rgb), timestamp_ms)
        rows = []
        for points, gestures, sides in zip(
            result.hand_landmarks, result.gestures, result.handedness, strict=True
        ):
            best = max(gestures, key=lambda g: g.score) if gestures else None
            side = max(sides, key=lambda s: s.score) if sides else None
            rows.append(
                {
                    "gesture": best.category_name if best else "None",
                    "confidence": best.score if best else 0,
                    "side": side.category_name if side else "unknown",
                    "landmarks": [[p.x, p.y, p.z] for p in points],
                }
            )
        return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify or download the locked official local perception models"
    )
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--directory", type=Path, default=Path("local-data/models"))
    args = parser.parse_args()
    paths = model_paths(args.directory, download=args.download)
    print("Verified model assets: " + ", ".join(paths))
