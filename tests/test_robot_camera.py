"""Synthetic source pixels verify independent preview/full resolution and clearing."""

import io

import numpy as np
import pytest
from PIL import Image

from reachy_brain.robot.camera import CameraFeed


@pytest.mark.features("D2", "D3", "V1", "V2", "P3")
@pytest.mark.scenario("ROBOT-CAMERA-SEPARATE-VIEWS")
def test_full_resolution_survives_preview_and_generation_clear():
    source = np.zeros((1080, 1920, 3), dtype=np.uint8)
    feed = CameraFeed(lambda: source)
    try:
        feed.set_enabled(True)
        full, meta = feed.snapshot()
        preview, _ = feed.snapshot(preview=True)
        assert Image.open(io.BytesIO(full)).size == (1920, 1080)
        assert Image.open(io.BytesIO(preview)).size == (640, 360)
        assert not meta["capture_time_known"]
        generation = meta["generation"]
        feed.set_enabled(False)
        assert feed.latest is None and not feed.encoded
        feed.set_enabled(True)
        _, replacement = feed.snapshot()
        assert replacement["generation"] > generation
    finally:
        feed.close()
    assert not feed.thread.is_alive()


@pytest.mark.features("D2", "D3", "P3", "P10")
@pytest.mark.scenario("ROBOT-PREVIEW-TIMING-BOUND")
@pytest.mark.asyncio
async def test_preview_deduplicates_and_preserves_unknown_timing():
    from types import SimpleNamespace

    from reachy_brain.robot.session import RobotSession

    source = SimpleNamespace(id="robot", generation=1, enabled=True)
    core = SimpleNamespace(mode="aware")
    calls, delivered = [], []

    class Edge:
        def __init__(self, *args, **kwargs):
            pass

        async def snapshot(self, *, preview=False):
            assert preview
            calls.append(preview)
            if len(calls) == 3:
                core.mode = "idle"
            return b"synthetic-preview", {
                "retrieved": {"time": 10, "stale": False, "uncertainty": 0.02},
                "sequence": 1 if len(calls) < 3 else 2,
            }

    async def receive(*args):
        delivered.append(args)

    from reachy_brain.config import Settings

    settings = Settings(_env_file=None)
    session = RobotSession(settings, None, core, receive, client_factory=Edge, on_preview=receive)
    session.source = source
    await session.previews()
    assert len(calls) == 3 and len(delivered) == 1
    assert all(item[-2] == 1.0 and item[-1] is True for item in delivered)
    assert all(item[0] is source and item[1] == 1 for item in delivered)
