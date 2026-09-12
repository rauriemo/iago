"""Real image transforms on synthetic colored corners; no physical readability claim."""

import io

import pytest
from PIL import Image, ImageDraw

from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V1", "V2", "V8", "V9")
@pytest.mark.scenario("VISUAL-ORIENTATION-PROVENANCE")
@pytest.mark.parametrize(
    "orientation,corner",
    [
        (1, "red"),
        (2, "lime"),
        (3, "yellow"),
        (4, "blue"),
        (5, "red"),
        (6, "blue"),
        (7, "yellow"),
        (8, "lime"),
    ],
)
def test_oriented_dimensions_crops_and_safe_metadata(orientation, corner):
    image = Image.new("RGB", (100, 60), "red")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 0, 99, 29), fill="lime")
    draw.rectangle((0, 30, 49, 59), fill="blue")
    draw.rectangle((50, 30, 99, 59), fill="yellow")
    exif = Image.Exif()
    exif[274] = orientation
    exif[315] = "Private artist metadata must not be retained"
    raw = io.BytesIO()
    image.save(raw, format="PNG", exif=exif)
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "upload", "Board")
    frame = store.add(source.id, 0, 99, store.prepare(raw.getvalue()))
    assert (frame.width, frame.height) == ((60, 100) if orientation >= 5 else (100, 60))
    meta = store.describe(frame)["transformation"]
    assert meta == {
        "input_width": 100,
        "input_height": 60,
        "input_format": "PNG",
        "exif_orientation": orientation,
        "orientation_applied": orientation != 1,
        "stored_format": "JPEG",
        "stored_color_mode": "RGB",
        "crop_coordinate_space": "stored_oriented_pixels",
    }
    with Image.open(io.BytesIO(frame.image)) as stored:
        expected = Image.new("RGB", (1, 1), corner).getpixel((0, 0))
        assert all(abs(a - b) < 5 for a, b in zip(stored.getpixel((10, 10)), expected, strict=True))
        assert not stored.getexif()
    assert frame.transformation_bytes > 0
    assert "Private artist" not in str(store.describe(frame))


@pytest.mark.features("V1", "V2")
@pytest.mark.scenario("VISUAL-INVALID-ORIENTATION")
def test_invalid_orientation_cannot_silently_describe_identity():
    image = Image.new("RGB", (10, 10))
    exif = Image.Exif()
    exif[274] = 9
    raw = io.BytesIO()
    image.save(raw, format="PNG", exif=exif)
    with pytest.raises(ToolError, match="invalid_image_orientation"):
        VisualStore.prepare(raw.getvalue())
