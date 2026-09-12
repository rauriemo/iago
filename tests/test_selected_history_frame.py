"""Exact historical image routing through the real controller with synthetic providers."""

import io
import time

import pytest
from PIL import Image

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.registry import (
    ActionPolicy,
    Rule,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("V2", "V4", "V8", "V9", "C4", "E1")
@pytest.mark.scenario("HISTORY-EXACT-FRAME-CONVERSATION")
@pytest.mark.parametrize(
    "case", ["valid", "cleared", "clear_during_stop", "crop", "denied_crop", "bad_crop"]
)
async def test_selected_history_frame_is_exact_and_never_substituted(case, tmp_path):
    visual = VisualStore()
    source = visual.source("synthetic", "screen", "Screen")
    current = visual.source("synthetic", "camera", "Current camera")
    data = io.BytesIO()
    Image.new("RGB", (30, 20), "red").save(data, format="PNG")
    prepared = visual.prepare(data.getvalue())
    now = time.time()
    older = visual.add(source.id, 0, now - 50, prepared)
    blue = io.BytesIO()
    Image.new("RGB", (30, 20), "blue").save(blue, format="PNG")
    newer = visual.add(current.id, 0, now - 1, visual.prepare(blue.getvalue()))
    calls, events = [], []

    class Brain:
        async def stream(self, messages, tools):
            calls.append(messages)
            if False:
                yield None

    class Voice:
        async def stream(self, text):
            if False:
                yield b""

    async def send(event):
        events.append(event)

    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, visual, None, Notes(tmp_path / "notes.sqlite"))
    if case == "denied_crop":
        policy.set(Rule("visual__session__inspect_region", "read", "deny"))
    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {"openai": Voice()},
        ToolExecutor(registry, policy, None),
        visual,
        send,
    )
    core.mode = "conversation"
    core.selected_source = current.id
    if case == "cleared":
        visual.clear(source.id)
    if case == "clear_during_stop":
        original_stop = core.stop

        async def stop_and_clear():
            await original_stop()
            visual.clear(source.id)

        core.stop = stop_and_clear
    try:
        if case in {"cleared", "clear_during_stop", "bad_crop"}:
            with pytest.raises(ToolError):
                await core.user_turn(
                    "Inspect the earlier image",
                    frame_id=older.id,
                    region=[0, 0, 999, 10] if case == "bad_crop" else None,
                )
            assert not calls and not core.history
        else:
            region = [2, 3, 12, 10] if case in {"crop", "denied_crop"} else None
            await core.user_turn("Inspect the earlier image", frame_id=older.id, region=region)
            await core.task
            if case == "denied_crop":
                assert not calls and any(e["type"] == "error" for e in events)
                return
            assert calls
            model_images = [
                part
                for message in calls[0]
                if isinstance(message.get("content"), list)
                for part in message["content"]
                if part.get("type") == "input_image"
            ]
            expected = [visual.image_input(older.id)]
            if region:
                expected.append(visual.image_input(older.id, region))
                assert core.evidence[-1]["region"] == region
            assert model_images == expected
            assert {f["id"] for f in core.evidence} == {older.id}
            assert core.selected_source == current.id
            await core.user_turn("Now the current view")
            await core.task
            assert [f["id"] for f in core.evidence] == [newer.id]
    finally:
        await core.stop()
        await core.executor.close()
