"""Actual tool image outputs on synthetic retained pixels, not optical qualification."""

import base64
import io

import pytest
from PIL import Image

from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import ActionPolicy, CallContext, ToolExecutor, ToolRegistry
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V4", "V6", "E1")
@pytest.mark.scenario("VISUAL-INSPECT-DETAIL-BUDGET")
@pytest.mark.parametrize("detail", [None, "full", "preview", "unknown"])
async def test_inspection_detail_and_batch_budget(tmp_path, detail):
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "screen", "Screen")
    data = io.BytesIO()
    Image.new("RGB", (640, 480), "blue").save(data, format="PNG")
    prepared = store.prepare(data.getvalue())
    frames = [store.add(source.id, 0, at, prepared) for at in (97, 98, 99)]
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, store, None, Notes(tmp_path / "notes.sqlite"))
    executor = ToolExecutor(registry, policy, None)
    ctx = CallContext("synthetic", 0)
    payload = {"ids": [frame.id for frame in frames]}
    if detail is not None:
        payload["detail"] = detail
    result = await executor.execute("visual__session__inspect_frames", payload, ctx)
    if detail == "unknown":
        assert result["status"] == "invalid_input"
        assert not ctx.attachments and not ctx.evidence and not ctx.budgets
        return
    assert result["status"] == "ok"
    assert result["result"]["detail"] == (detail or "full")
    assert [f["id"] for f in result["result"]["frames"]] == payload["ids"]
    assert [f["image_sha256"] for f in ctx.evidence] == [f.image_sha256 for f in frames]
    for item in ctx.attachments[1::2]:
        raw = base64.b64decode(item["image_url"].split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            assert image.size == ((320, 240) if detail == "preview" else (640, 480))
    key, maximum = ("previews", 24) if detail == "preview" else ("details", 4)
    assert ctx.budgets[key] == 3
    ctx.budgets[key] = maximum - 1
    before = list(ctx.attachments)
    result = await executor.execute("visual__session__inspect_frames", payload, ctx)
    assert result["status"] == "retrieval_budget"
    assert ctx.attachments == before and len(ctx.evidence) == 3
    assert ctx.budgets[key] == maximum - 1
    invalid = await executor.execute(
        "visual__session__inspect_frames",
        {"ids": [frames[0].id, "expired"]},
        CallContext("synthetic", 1),
    )
    assert invalid["status"] == "expired"
