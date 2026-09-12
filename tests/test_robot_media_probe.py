"""Synthetic media exercises qualification measurements, not a hardware pass."""

import io

import numpy as np
import pytest
from PIL import Image

from reachy_brain.evals.robot_media import probe_media


class Media:
    input_rate = output_rate = 16000
    input_channels = output_channels = 1

    def __init__(self):
        self.closed = False
        self.flushed = False

    def frame(self):
        return np.zeros((24, 32, 3), dtype=np.uint8)

    def snapshot(self):
        out = io.BytesIO()
        Image.new("RGB", (64, 48)).save(out, format="JPEG")
        return out.getvalue()

    def capture(self):
        return np.zeros((160, 1), dtype=np.float32)

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True


@pytest.mark.features("D2", "D3", "P9")
@pytest.mark.scenario("ROBOT-MEDIA-PROBE-MEASUREMENTS")
def test_probe_records_actual_samples_and_releases_owner():
    media = Media()
    tick = [0.0]

    def sleep(seconds):
        tick[0] += seconds

    result = probe_media(lambda: media, duration=0.1, clock=lambda: tick[0], sleep=sleep)
    assert result["frame_count"] >= 2 and result["audio_chunk_count"] >= 2
    assert result["distinct_frame_hashes"] == 1
    assert result["snapshot_dimensions"] == [64, 48]
    assert result["microphone_peak"] == 0
    assert result["audio_after_flush"]
    assert media.closed and media.flushed
    assert "image" not in result and "pcm" not in result


@pytest.mark.features("D2", "D3", "P9")
@pytest.mark.scenario("ROBOT-MEDIA-PROBE-INVALID-DATA")
@pytest.mark.parametrize("case", ["camera", "audio", "snapshot", "missing", "after_flush"])
def test_invalid_media_fails_and_releases_owner(case):
    media = Media()
    if case == "camera":
        media.frame = lambda: np.zeros((3, 4), dtype=np.uint8)
    elif case == "audio":
        media.capture = lambda: np.array([float("nan")], dtype=np.float32)
    elif case == "snapshot":
        media.snapshot = lambda: b"invalid"
    elif case == "after_flush":
        media.capture = lambda: None if media.flushed else np.zeros(160, dtype=np.float32)
    else:
        media.capture = lambda: None
    tick = [0.0]

    def sleep(seconds):
        tick[0] += seconds

    with pytest.raises((ValueError, OSError)):
        probe_media(lambda: media, duration=0.1, clock=lambda: tick[0], sleep=sleep)
    assert media.closed
