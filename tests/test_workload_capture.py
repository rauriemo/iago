"""Synthetic capture clocks and actual application metadata, not a physical soak."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.evals.workload_capture import record
from reachy_brain.web.app import create_app


@pytest.mark.features("D6", "V9", "E1")
@pytest.mark.scenario("WORKLOAD-METADATA-PRIVACY")
def test_metadata_endpoint_excludes_content_and_credentials(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, robot_camera_hf_token="synthetic-private-hf"),
        token="test",
    )
    with TestClient(app) as client:
        assert client.get("/api/workload-status").status_code == 401
        app.state.visual.source("private-owner", "screen", "Private document title")
        response = client.get("/api/workload-status", headers={"Authorization": "Bearer test"})
        assert response.status_code == 200
        value = response.json()
        assert value["version"] == 1 and value["mode"] == "idle"
        assert set(value["timings"]) == {
            "project",
            "tools",
            "response",
            "capture",
            "perception",
            "retrieval",
            "gesture",
        }
        assert value["timings"]["tools"]["total"] == 0
        assert value["timings"]["project"]["total"] == 0
        assert value["sources"][0]["kind"] == "screen"
        assert value["sources"][0]["retained_frames"] == 0
        assert value["physical_qualification"] is False
        from reachy_brain.evals.workload_resources import BOUND_NAMES

        assert set(value["bounds"]) == BOUND_NAMES
        assert value["bounds"]["index_bytes"]["used"] > 0
        assert value["bounds"]["journal_bytes"]["used"] > 0
        assert all(0 <= pair["used"] <= pair["limit"] for pair in value["bounds"].values())
        for private in ("Private document title", "private-owner", "synthetic-private-hf"):
            assert private not in response.text


@pytest.mark.features("D6", "V9", "E1")
@pytest.mark.scenario("WORKLOAD-CAPTURE-BOUNDS-OWNERSHIP")
@pytest.mark.parametrize("failure", [None, "owner", "oversized", "http"])
async def test_bounded_capture_completion_and_incomplete_evidence(tmp_path, failure):
    ticks = [0.0]
    calls = 0

    async def sleep(delay):
        ticks[0] += delay

    def handler(request):
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/workload-status"
        if calls == 2 and failure == "oversized":
            return httpx.Response(200, content=b"x" * 131073)
        if calls == 2 and failure == "http":
            return httpx.Response(500, text="private error body")
        return httpx.Response(
            200,
            json={
                "version": 1,
                "owner": "new" if calls == 2 and failure == "owner" else "first",
                "physical_qualification": False,
            },
        )

    output = tmp_path / "capture.jsonl"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        if failure:
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                await record(
                    client, output, duration=10, interval=5, clock=lambda: ticks[0], sleep=sleep
                )
        else:
            assert (
                await record(
                    client, output, duration=10, interval=5, clock=lambda: ticks[0], sleep=sleep
                )
                == 3
            )
        before = output.read_bytes()
        with pytest.raises(FileExistsError):
            await record(client, output, duration=10)
        assert output.read_bytes() == before
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["type"] == ("incomplete" if failure else "complete_capture")
    assert rows[0]["physical_qualification"] is False
    assert "private error body" not in output.read_text(encoding="utf-8")
    if not failure:
        assert [row["elapsed"] for row in rows if row["type"] == "sample"] == [0, 5, 10]
        assert rows[-1]["physical_qualification"] is False


@pytest.mark.features("D6", "V9", "E1")
@pytest.mark.scenario("WORKLOAD-ACTUAL-APP-CAPTURE")
async def test_recorder_uses_actual_authenticated_application(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), token="synthetic-local")
    output = tmp_path / "observed.jsonl"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": "Bearer synthetic-local"},
        ) as client:
            assert await record(client, output, duration=1, interval=1) == 2
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    observed = [row["observation"] for row in rows if row["type"] == "sample"]
    assert len({item["owner"] for item in observed}) == 1
    assert observed[1]["elapsed_seconds"] > observed[0]["elapsed_seconds"]
    assert all(item["mode"] == "idle" and not item["physical_qualification"] for item in observed)
    assert "synthetic-local" not in output.read_text(encoding="utf-8")
