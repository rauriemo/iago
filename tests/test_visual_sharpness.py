"""Synthetic image degradation and actual retrieval ranking, not optical qualification."""

import io
import math

import pytest
from PIL import Image, ImageDraw, ImageFilter

from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import ActionPolicy, CallContext, ToolExecutor, ToolRegistry
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


def prepare(image):
    data = io.BytesIO()
    image.save(data, format="PNG")
    return VisualStore.prepare(data.getvalue())


@pytest.mark.features("V2", "V4", "V6", "E1")
@pytest.mark.scenario("VISUAL-MEASURED-SHARPNESS-RANK")
async def test_measured_blur_changes_equal_time_candidate_ranking(tmp_path):
    image = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 320, 16):
        draw.rectangle((x, 0, x + 7, 239), fill="black")
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Synthetic")
    sharp = store.add(source.id, 0, 98, prepare(image))
    blurred = store.add(source.id, 0, 100, prepare(image.filter(ImageFilter.GaussianBlur(3))))
    assert math.isfinite(sharp.sharpness) and sharp.sharpness > blurred.sharpness > 0
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, store, None, Notes(tmp_path / "notes.sqlite"))
    executor = ToolExecutor(registry, policy, None)
    result = await executor.execute(
        "visual__session__search_visual_history",
        {"source": source.id, "near": 99},
        CallContext("synthetic", 0),
    )
    assert result["status"] == "ok"
    frames = result["result"]["frames"]
    assert [frame["id"] for frame in frames] == [sharp.id, blurred.id]
    assert frames[0]["sharpness"] == sharp.sharpness
    assert frames[0]["sharpness_method"] == "thumbnail_grayscale_gradient_energy_v1"
    closer = store.search(source=source.id, near=100)
    assert closer["frames"][0]["id"] == blurred.id
    assert "does not prove legibility" in closer["coverage"]


@pytest.mark.features("V2", "V4", "D5")
@pytest.mark.scenario("VISUAL-SHARPNESS-DEGENERATE-DIMENSIONS")
@pytest.mark.parametrize("size", [(1, 1), (1, 40), (40, 1), (640, 480)])
def test_flat_and_single_axis_images_have_finite_zero_score(size):
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "upload", "Synthetic")
    frame = store.add(source.id, 0, 100, prepare(Image.new("RGB", size, "gray")))
    assert frame.sharpness == 0.0
