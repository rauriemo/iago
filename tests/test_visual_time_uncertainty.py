"""Synthetic timestamp intervals; no calibrated camera timing claim."""

import pytest
from test_visual_store import image

from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V5", "V6")
@pytest.mark.scenario("VISUAL-TIME-UNCERTAINTY-RETRIEVAL")
def test_interval_overlap_expands_search_browse_and_speech_index():
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    frame = store.add(source.id, 0, 94, image(), capture_uncertainty=3)
    assert [f["id"] for f in store.search(start=97, end=98)["frames"]] == [frame.id]
    assert [f["id"] for f in store.browse(start=97, end=98)["frames"]] == [frame.id]
    assert not store.search(start=97.01, end=98)["frames"]
    store.associate_speech("emerald", 99, 99, {source.id: 0})
    result = store.search(query="emerald", near=96)["frames"][0]
    assert result["id"] == frame.id
    assert result["capture_uncertainty_seconds"] == 3
    assert result["rank"]["distance_seconds"] == 0
    assert result["capture_interval"] == [91, 97]


@pytest.mark.features("V5", "V6")
@pytest.mark.scenario("VISUAL-TIME-UNCERTAINTY-VALIDATION")
@pytest.mark.parametrize("invalid", [-1, True, "1", float("nan"), float("inf"), 601])
def test_invalid_bound_cannot_mutate_store_or_capture_sequence(invalid):
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    with pytest.raises(ToolError, match="invalid_capture_uncertainty"):
        store.add(source.id, 0, 99, image(), capture_uncertainty=invalid)
    assert not store.frames and source.last_capture == -1


@pytest.mark.features("V5", "V6")
@pytest.mark.scenario("VISUAL-TIME-UNKNOWN-BOUND")
def test_missing_bound_is_explicit_and_does_not_invent_precision():
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    frame = store.add(source.id, 0, 99, image())
    result = store.describe(frame)
    assert result["capture_uncertainty_seconds"] is None
    assert result["capture_interval"] is None
    frame.capture_time_known = False
    frame.capture_uncertainty = 1
    assert store.describe(frame)["capture_interval"] is None
    store.associate_speech("emerald", 99, 99, {source.id: 0})
    assert not frame.speech_terms


@pytest.mark.features("V5", "V6", "V9")
@pytest.mark.scenario("VISUAL-TIME-UNCERTAINTY-HTTP")
@pytest.mark.parametrize("bound", [None, "0.25", "nan", "-1", "601"])
async def test_http_retains_valid_bound_and_rejects_invalid_before_decode(
    tmp_path, monkeypatch, bound
):
    import time

    import httpx

    from reachy_brain.config import Settings
    from reachy_brain.web.app import create_app

    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    visual = app.state.visual
    source = visual.source("synthetic", "camera", "Camera")
    calls = []

    def prepare(data):
        calls.append(data)
        return image()

    monkeypatch.setattr(visual, "prepare", prepare)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer test"},
        ) as client,
    ):
        headers = {"x-captured-at": str(time.time())}
        if bound is not None:
            headers["x-capture-uncertainty"] = bound
        response = await client.post(
            f"/api/frame/{source.id}/0", content=b"synthetic", headers=headers
        )
        if bound in (None, "0.25"):
            assert response.status_code == 200
            assert response.json()["capture_uncertainty_seconds"] == (
                None if bound is None else 0.25
            )
            assert len(calls) == 1
        else:
            assert response.status_code == 400
            assert not calls and not visual.frames
