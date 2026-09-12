"""Local Reachy media acquisition probe; uses the real adapter's stop/close cleanup."""

import argparse
import hashlib
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image


def probe_media(factory, *, duration=5.0, clock=time.monotonic, sleep=time.sleep):
    if not 0 < duration <= 10:
        raise ValueError("probe_duration_limit")
    media = factory()
    frames = audio_chunks = audio_samples = 0
    hashes = set()
    peak = 0.0
    dimensions = set()
    started = clock()

    def audio_count(value):
        nonlocal peak, audio_samples, audio_chunks
        if value is None:
            return False
        samples = np.asarray(value)
        if not samples.size:
            return False
        if (
            samples.ndim not in (1, 2)
            or samples.size > 1920000
            or samples.dtype.kind not in "fi"
            or not np.isfinite(samples).all()
        ):
            raise ValueError("invalid_microphone_samples")
        audio_chunks += 1
        audio_samples += samples.size
        peak = max(peak, float(np.max(np.abs(samples.astype(float)))))
        return True

    try:
        while clock() - started < duration:
            frame = media.frame()
            if frame is not None:
                pixels = np.asarray(frame)
                if (
                    pixels.dtype != np.uint8
                    or pixels.ndim != 3
                    or pixels.shape[2] != 3
                    or not pixels.size
                    or pixels.nbytes > 32 * 1024 * 1024
                ):
                    raise ValueError("invalid_camera_frame")
                frames += 1
                dimensions.add((pixels.shape[1], pixels.shape[0]))
                hashes.add(hashlib.sha256(pixels.tobytes()).digest())
            audio_count(media.capture())
            sleep(0.02)
        snapshot = media.snapshot()
        if not isinstance(snapshot, bytes) or not 0 < len(snapshot) <= 20 * 1024 * 1024:
            raise ValueError("invalid_snapshot")
        with Image.open(io.BytesIO(snapshot)) as image:
            if image.format != "JPEG" or image.width * image.height > 24_000_000:
                raise ValueError("invalid_snapshot_format")
            image.verify()
            snapshot_dimensions = [image.width, image.height]
        media.flush()
        after_flush = False
        flush_started = clock()
        while clock() - flush_started < 2:
            if audio_count(media.capture()):
                after_flush = True
                break
            sleep(0.02)
        if frames < 2 or audio_chunks < 2 or not after_flush:
            raise ValueError("insufficient_media_samples")
        return dict(
            frame_count=frames,
            distinct_frame_hashes=len(hashes),
            frame_dimensions=[list(d) for d in sorted(dimensions)],
            audio_chunk_count=audio_chunks,
            audio_scalar_samples=audio_samples,
            microphone_peak=peak,
            audio_after_flush=after_flush,
            snapshot_dimensions=snapshot_dimensions,
            input_rate=media.input_rate,
            output_rate=media.output_rate,
            input_channels=media.input_channels,
            output_channels=media.output_channels,
            elapsed_seconds=clock() - started,
            scope="SDK-delivered media and post-flush capture; no acoustic, motion, freshness or effective detector-rate qualification",
        )
    finally:
        media.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from reachy_brain.robot.media import ReachyLocalMedia

    result = probe_media(ReachyLocalMedia)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out)


if __name__ == "__main__":
    main()
