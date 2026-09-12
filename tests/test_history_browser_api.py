"""Bounded historical browsing over actual authenticated HTTP, with synthetic images."""

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("V3", "V4", "V6", "V9", "D4")
@pytest.mark.scenario("HISTORY-HTTP-PAGES-AND-CLEAR")
def test_history_filters_pages_and_retired_cursor(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    visual = app.state.visual
    source = visual.source("synthetic", "screen", "Desk")
    other = visual.source("synthetic", "camera", "Camera")
    data = io.BytesIO()
    Image.new("RGB", (20, 20)).save(data, format="PNG")
    prepared = visual.prepare(data.getvalue())
    now = time.time()
    frames = [visual.add(source.id, 0, now - 100 + i, prepared) for i in range(30)]
    visual.add(other.id, 0, now, prepared)
    frames[3].labels = ["diagram"]
    with TestClient(app) as client:
        assert client.get("/api/history").status_code == 401
        client.headers["Authorization"] = "Bearer test"
        page = client.get("/api/history", params={"source": source.id}).json()
        coverage = {row["id"]: row["retained"] for row in page["sources"]}
        assert coverage[source.id]["rolling_frames"] == 30
        assert coverage[source.id]["earliest_capture"] == frames[0].captured
        assert coverage[source.id]["latest_capture"] == frames[-1].captured
        assert coverage[source.id]["rolling_bytes"] == sum(frame.size for frame in frames)
        assert coverage[other.id]["rolling_frames"] == 1
        preview = client.get(f"/api/frame/{frames[0].id}?thumbnail=true")
        assert 0 < float(preview.headers["X-Iago-Expires-In"]) <= visual.retention
        visual.pin(frames[0].id, "Reference")
        pinned_coverage = {row["id"]: row["retained"] for row in visual.source_status()}
        assert pinned_coverage[source.id]["pin_bytes"] == frames[0].size
        assert pinned_coverage[source.id]["pin_frames"] == 1
        assert pinned_coverage[source.id]["earliest_capture"] == frames[1].captured
        assert client.get(f"/api/frame/{frames[0].id}").headers["X-Iago-Expires-In"] == "pinned"
        assert [f["id"] for f in page["frames"]] == [f.id for f in reversed(frames[-24:])]
        following = client.get(
            "/api/history", params={"source": source.id, "before": page["next_before"]}
        ).json()
        assert [f["id"] for f in following["frames"]] == [f.id for f in reversed(frames[:6])]
        assert following["next_before"] is None
        found = client.get(
            "/api/history", params={"query": "diagram", "start": now - 98, "end": now - 95}
        ).json()
        assert [f["id"] for f in found["frames"]] == [frames[3].id]
        assert client.get("/api/history", params={"start": "nan"}).status_code == 400
        assert client.get("/api/history", params={"query": "x" * 501}).status_code == 422
        visual.clear(source.id, disable=True)
        assert client.get("/api/history", params={"before": page["next_before"]}).status_code == 409
        assert client.get("/api/history", params={"source": source.id}).json()["frames"] == []
