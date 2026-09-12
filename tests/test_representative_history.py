"""Synthetic archive coverage through the real visual registry/executor."""

import io

import pytest
from PIL import Image

from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import ActionPolicy, CallContext, ToolExecutor, ToolRegistry
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V4", "V6", "V9", "E1")
@pytest.mark.scenario("VISUAL-REPRESENTATIVE-WINDOW")
async def test_representative_window_and_neighbor_expansion(tmp_path):
    visual = VisualStore(clock=lambda: 600.0)
    camera = visual.source("synthetic", "camera", "Camera")
    screen = visual.source("synthetic", "screen", "Screen")
    data = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(data, format="PNG")
    prepared = visual.prepare(data.getvalue())
    for second in range(1, 601):
        visual.add(camera.id, 0, second, prepared)
        visual.add(screen.id, 0, second, prepared)
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, visual, None, Notes(tmp_path / "notes.sqlite"))
    executor = ToolExecutor(registry, policy, None)
    ctx = CallContext("synthetic", 0)
    key = "visual__session__browse_visual_history"
    result = await executor.execute(key, {"source": camera.id}, ctx)
    assert result["status"] == "ok"
    overview = result["result"]
    frames = overview["frames"]
    times = [f["captured"] for f in frames]
    assert len(frames) == len(set(times)) == 8
    assert times[0] == 600 and times[-1] == 1
    assert max(a - b for a, b in zip(times, times[1:], strict=False)) <= 86
    assert all(f["source"] == camera.id for f in frames)
    assert overview["matching_frames"] == 600 and overview["unsampled_frames"] == 592
    assert overview["next_cursor"] is None
    assert len(ctx.attachments) == 16 and ctx.budgets["previews"] == 8
    candidate = times[4]
    neighbors = await executor.execute(
        key,
        {
            "source": camera.id,
            "start": candidate - 2,
            "end": candidate + 2,
            "sampling": "recent",
            "limit": 3,
        },
        ctx,
    )
    assert neighbors["status"] == "ok"
    assert [f["captured"] for f in neighbors["result"]["frames"]] == [
        candidate + 2,
        candidate + 1,
        candidate,
    ]
    assert neighbors["result"]["next_cursor"] == 3
    last = await executor.execute(
        key,
        {
            "source": camera.id,
            "start": candidate - 2,
            "end": candidate + 2,
            "sampling": "recent",
            "limit": 3,
            "cursor": 3,
        },
        ctx,
    )
    assert [f["captured"] for f in last["result"]["frames"]] == [candidate - 1, candidate - 2]
    assert ctx.budgets["previews"] == 13
    denied = await executor.execute(key, {"source": camera.id}, ctx)
    assert denied["status"] == "retrieval_budget"
    assert ctx.budgets["previews"] == 13
    bad = await executor.execute(key, {"cursor": 1}, CallContext("synthetic", 1))
    assert bad["status"] == "invalid_input"
    visual.clear(camera.id)
    cleared = await executor.execute(key, {"source": camera.id}, CallContext("synthetic", 2))
    assert cleared["result"]["frames"] == []
    assert cleared["result"]["matching_frames"] == 0
