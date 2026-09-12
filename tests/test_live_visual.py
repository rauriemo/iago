"""Real Astra image transport on synthetic shapes; no optical qualification claim."""

import hashlib
import io
import json
import time

import pytest
from PIL import Image, ImageDraw

from reachy_brain.config import Settings
from reachy_brain.providers.live import AstraBrain, ProviderGate
from reachy_brain.providers.usage_storage import UsageStorage
from reachy_brain.vision.store import VisualStore


@pytest.mark.live_provider
@pytest.mark.features("C4", "V2", "V4")
@pytest.mark.scenario("ASTRA-SYNTHETIC-IMAGE-ROUNDTRIP")
@pytest.mark.parametrize("stored_format", ["jpeg", "png"])
async def test_astra_image_overview_and_crop_roundtrip(record_property, stored_format, live_gate):
    settings = Settings()
    if not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY missing in private setup")
    if settings.iago_development_budget == 0:
        pytest.skip("Live development budget is zero")
    assert settings.brain_model == "gpt-6-astra"
    visual = VisualStore()
    source = visual.source(
        "synthetic", "upload" if stored_format == "png" else "camera", "Synthetic shapes"
    )
    raster = Image.new("RGB", (640, 320), "white")
    draw = ImageDraw.Draw(raster)
    draw.rectangle((60, 60, 240, 240), fill="red")
    draw.ellipse((400, 60, 580, 240), fill="blue")
    data = io.BytesIO()
    raster.save(data, format="PNG")
    fixture = data.getvalue()
    frame = visual.add(
        source.id, 0, time.time(), visual.prepare(fixture, preserve_png=stored_format == "png")
    )
    overview = await visual.image_input_async(frame.id)
    crop = await visual.image_input_async(frame.id, region=[360, 20, 260, 280])
    assert overview["image_url"].startswith(f"data:image/{stored_format};base64,")
    assert crop["image_url"].startswith(f"data:image/{stored_format};base64,")
    expected = {"left": "red square", "right": "blue circle", "crop": "blue circle"}
    gate = live_gate
    storage = gate.persistence
    brain = AstraBrain(settings, gate)
    text = ""
    done = False
    provenance = []
    try:
        async for event in brain.stream(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "The first image is an overview; the second is a crop. "
                                "Identify each colored shape. Return only a JSON object with keys "
                                "left, right (positions in the overview), and crop. Each value must "
                                "be two lowercase English words: its color then its shape."
                            ),
                        },
                        overview,
                        crop,
                    ],
                }
            ],
            [],
            max_tokens=512,
        ):
            if event.get("provenance") not in provenance:
                provenance.append(event.get("provenance"))
            if event["type"] == "text":
                text += event["text"]
            elif event["type"] == "done":
                done = True
        await storage.close()
        recovered = ProviderGate(settings)
        reopened = UsageStorage(recovered, storage.path)
        await reopened.start()
        try:
            assert recovered.estimated_usd == gate.estimated_usd
            assert recovered.unknown_charges == gate.unknown_charges
            assert recovered.seen_usage == gate.seen_usage
            assert not recovered.active
            checkpoint_status = reopened.status()
            assert checkpoint_status["status"] == "saved"
        finally:
            await reopened.close()
        record_property("sample_count", 1)
        record_property(
            "measurements",
            {
                "fixture": "synthetic geometric shapes; not physical camera or screen media",
                "fixture_sha256": hashlib.sha256(fixture).hexdigest(),
                "stored_image_sha256": frame.image_sha256,
                "stored_format": stored_format,
                "overview_dimensions": [640, 320],
                "crop_region": [360, 20, 260, 280],
                "image_detail": "high",
                "expected": expected,
                "observed_text": text,
                "model": settings.brain_model,
                "usage_attempts": len(gate.usage),
                "completed": done,
                "response_provenance": provenance,
                "checkpoint_reopened": checkpoint_status,
                "known_estimated_usd": recovered.estimated_usd,
                "unknown_charges": recovered.unknown_charges,
            },
        )
        assert done
        assert json.loads(text) == expected
        assert len(gate.usage) == 1
        assert provenance == [
            {
                "response_id": gate.usage[0]["request_id"],
                "requested_model": settings.brain_model,
                "reported_model": settings.brain_model,
            }
        ]
        assert provenance[0]["response_id"]
    finally:
        await brain.close()
        await storage.close()
