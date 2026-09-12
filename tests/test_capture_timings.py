"""Real archive HTTP path with synthetic images; backend time is not exposure time."""

import asyncio
import io
import time

import httpx
import pytest
from PIL import Image

from reachy_brain.config import Settings
from reachy_brain.core.timing import StageTimings
from reachy_brain.evals.workload_timings import summarize
from reachy_brain.web.app import create_app


@pytest.mark.features("V1", "V9", "D6")
@pytest.mark.scenario("ARCHIVE-BACKEND-TIMINGS")
async def test_actual_archive_success_error_and_private_timing(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="synthetic")
    source = app.state.visual.source("private-owner", "screen", "private-window-title")
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(image, format="PNG")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer synthetic"},
        ) as client,
    ):
        url = f"/api/frame/{source.id}/{source.generation}"
        result = await client.post(
            url, content=image.getvalue(), headers={"x-captured-at": str(time.time())}
        )
        assert result.status_code == 200
        failed = await client.post(
            url, content=b"invalid-image", headers={"x-captured-at": str(time.time())}
        )
        assert failed.status_code == 409
        response = await client.get("/api/workload-status")
        value = response.json()
        rows = value["timings"]["capture"]["samples"]
        assert [r["outcome"] for r in rows] == ["ok", "error"]
        assert all(r["operation"] == "screen_archive" and r["seconds"] >= 0 for r in rows)
        assert "private-window-title" not in response.text and "private-owner" not in response.text
        report = summarize([value, value])
        capture = [r for r in report["distributions"] if r["family"] == "capture"]
        assert len(capture) == 2 and all(r["count"] == 1 for r in capture)
        assert not report["acceptance_pass"]


@pytest.mark.features("P1", "V1", "D6")
@pytest.mark.scenario("STAGE-TIMINGS-CANCELLATION-BOUNDS")
def test_stage_measurements_preserve_cancellation_and_missing_values():
    now = [1.0]
    timing = StageTimings({"process_frame"}, "synthetic", clock=lambda: now[0])
    with pytest.raises(asyncio.CancelledError), timing.measure("process_frame"):
        now[0] = 1.5
        raise asyncio.CancelledError
    assert timing.snapshot()["samples"][0]["outcome"] == "canceled"
    assert timing.snapshot()["samples"][0]["seconds"] == 0.5
    assert not timing.record("process_frame", float("nan"))
    for _ in range(70):
        timing.record("process_frame", 0.02)
    snapshot = timing.snapshot()
    assert len(snapshot["samples"]) == 64 and snapshot["total"] == 72
    result = summarize([{"owner": "synthetic", "timings": {"perception": snapshot}}])
    assert result["status"] == "blocked" and result["missing_sequence_count"] == 8
    assert result["distributions"][0]["count"] == 64
