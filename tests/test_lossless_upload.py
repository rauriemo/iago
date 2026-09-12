"""Synthetic screenshot pixels through real HTTP storage and model materialization."""

import base64
import hashlib
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("V1", "V2", "V4", "V8", "V9")
@pytest.mark.scenario("LOSSLESS-PNG-UPLOAD-ROUNDTRIP")
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_upload_pixels_full_crop_mime_and_camera_format(tmp_path, mode):
    image = Image.new(mode, (31, 19))
    image.putdata([tuple((i * n) % 256 for n in range(1, len(mode) + 1)) for i in range(31 * 19)])
    info = PngImagePlugin.PngInfo()
    info.add_text("private", "do not retain")
    raw = io.BytesIO()
    image.save(raw, format="PNG", pnginfo=info)
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    store = app.state.visual
    upload = store.source("synthetic", "upload", "Screenshot")
    camera = store.source("synthetic", "camera", "Camera")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test", "x-captured-at": str(time.time())}
        responses = []
        for source in (upload, camera):
            response = client.post(
                f"/api/frame/{source.id}/0", content=raw.getvalue(), headers=headers
            )
            response.raise_for_status()
            responses.append(response.json())
        frame = store.get(responses[0]["id"])
        assert responses[0]["transformation"]["stored_format"] == "PNG"
        assert responses[1]["transformation"]["stored_format"] == "JPEG"
        original = client.get(f"/api/frame/{frame.id}", headers=headers)
        assert original.headers["content-type"] == "image/png"
        assert hashlib.sha256(original.content).hexdigest() == frame.image_sha256
        with Image.open(io.BytesIO(original.content)) as decoded:
            assert decoded.mode == mode and decoded.tobytes() == image.tobytes()
            assert "private" not in decoded.info
        assert (
            client.get(f"/api/frame/{frame.id}?thumbnail=true", headers=headers).headers[
                "content-type"
            ]
            == "image/jpeg"
        )
        for region in (None, [2, 3, 7, 5]):
            payload = store.image_input(frame.id, region)
            assert payload["image_url"].startswith("data:image/png;base64,")
            data = base64.b64decode(payload["image_url"].split(",", 1)[1])
            with Image.open(io.BytesIO(data)) as decoded:
                expected = image if region is None else image.crop((2, 3, 9, 8))
                assert decoded.tobytes() == expected.tobytes()
        store.pin(frame.id, "Screenshot")
        assert store.totals()["pin_bytes"] == frame.size
        store.clear(upload.id)
        assert client.get(f"/api/frame/{frame.id}", headers=headers).status_code == 409
        assert store.get(responses[1]["id"])


@pytest.mark.features("V2", "V8", "V9")
@pytest.mark.scenario("PNG-EVALUATION-ARTIFACT")
def test_png_artifact_hash_format_and_ambiguity(tmp_path):
    from test_visual_grounding import records

    from reachy_brain.evals.screen_grounding import digest, verify_images

    plan, capture, _ = records()
    data = io.BytesIO()
    Image.new("RGB", (3, 3), "white").save(data, "PNG")
    sha = hashlib.sha256(data.getvalue()).hexdigest()
    for case in plan.cases:
        for ref in case.references:
            ref.image_sha256 = sha
    for answer, case in zip(capture.answers, plan.cases, strict=True):
        answer.citations = case.references
    capture.plan_sha256 = digest(plan)
    path = tmp_path / f"{sha}.png"
    path.write_bytes(data.getvalue())
    assert verify_images(tmp_path, plan, capture)["unique_images"] == 1
    (tmp_path / f"{sha}.jpg").write_bytes(data.getvalue())
    with pytest.raises(ValueError, match="one_image_artifact_required"):
        verify_images(tmp_path, plan, capture)
