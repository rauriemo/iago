"""Synthetic detector evidence searched through the application executor."""

import io

import pytest
from PIL import Image

from reachy_brain.behavior.engine import Event
from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import ActionPolicy, CallContext, ToolExecutor, ToolRegistry
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.evidence import EventEvidence
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V4", "V6", "P2", "E1")
@pytest.mark.scenario("VISUAL-EVENT-LABEL-SEARCH")
async def test_event_label_search_ranking_and_retention(tmp_path):
    now = [100.0]
    store = VisualStore(clock=lambda: now[0], retention=60)
    camera = store.source("synthetic", "camera", "Camera")
    other = store.source("synthetic", "camera", "Other")
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(data, format="JPEG")
    evidence = EventEvidence(store)
    frame_ids = []
    for index, (source, at, kind, label) in enumerate(
        [
            (camera, 70, "object_appeared", "cell phone"),
            (camera, 90, "object_disappeared", "cell phone"),
            (camera, 99, "object_appeared", "cup"),
            (other, 98, "object_appeared", "cell phone"),
        ]
    ):
        events = await evidence.attach(
            [
                Event(
                    str(index),
                    source.id,
                    kind,
                    at,
                    0.9,
                    detector="synthetic",
                    details={"label": label},
                )
            ],
            data.getvalue(),
        )
        frame_ids.append(events[0].frames[0])
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, store, None, Notes(tmp_path / "notes.sqlite"))
    executor = ToolExecutor(registry, policy, None)
    key = "visual__session__search_visual_history"

    async def search(**payload):
        result = await executor.execute(key, payload, CallContext("synthetic", 0))
        assert result["status"] == "ok"
        return result["result"]

    result = await search(source=camera.id, object_labels=["CELL PHONE"], near=72)
    assert [f["id"] for f in result["frames"]] == frame_ids[:2]
    assert result["frames"][0]["rank"]["distance_seconds"] == 2
    assert result["frames"][0]["events"][0]["detector"] == "synthetic"
    filtered = await search(source=camera.id, event_types=["object_disappeared"], query="phone")
    assert [f["id"] for f in filtered["frames"]] == [frame_ids[1]]
    assert "does not prove legibility" in filtered["coverage"]
    assert (await search(source=camera.id, query="red spaceship"))["frames"] == []
    assert (await search(source=camera.id, start=95, object_labels=["cell phone"]))["frames"] == []
    now[0] = 131
    assert [
        f["id"] for f in (await search(source=camera.id, object_labels=["cell phone"]))["frames"]
    ] == [frame_ids[1]]
    store.clear(camera.id)
    assert (await search(source=camera.id))["frames"] == []
    assert [f["id"] for f in (await search(source=other.id))["frames"]] == [frame_ids[3]]
