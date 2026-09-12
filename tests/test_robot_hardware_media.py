"""Opt-in real robot-local SDK capture; no fake adapter can qualify this test."""

import importlib.util
import json
import os
import subprocess
import sys

import pytest


@pytest.mark.robot
@pytest.mark.features("D2", "D3", "P9")
@pytest.mark.scenario("ROBOT-LOCAL-HARDWARE-MEDIA")
def test_actual_robot_local_media(tmp_path, record_property):
    if os.environ.get("IAGO_ROBOT_LOCAL_MEDIA_CHECK") != "1":
        pytest.skip(
            "Run on the physical robot with IAGO_ROBOT_LOCAL_MEDIA_CHECK=1; see docs/iago/ROBOT_EVAL.md"
        )
    if sys.platform != "linux":
        pytest.skip("Robot-local Linux runtime required for this local SDK qualification")
    if importlib.util.find_spec("reachy_mini") is None or importlib.util.find_spec("gi") is None:
        pytest.skip("Robot SDK and local GStreamer Python bindings required")
    output = tmp_path / "robot-media.json"
    command = [sys.executable, "-m", "reachy_brain.evals.robot_media", "--output", str(output)]
    record_property(
        "expected",
        "Actual local camera/JPEG/microphone samples, capture after flush, bounded child completion",
    )
    # The process deadline also bounds SDK initialization/capture/shutdown calls.
    result = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False
    )
    assert result.returncode == 0, (
        f"Robot media probe failed with exit {result.returncode}; check daemon/media availability locally"
    )
    measurements = json.loads(output.read_text(encoding="utf-8"))
    record_property("sample_count", measurements["frame_count"])
    record_property("measurements", measurements)
    assert measurements["frame_count"] >= 2
    assert measurements["audio_chunk_count"] >= 2 and measurements["audio_after_flush"]
