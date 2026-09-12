"""Actual controller preflight with synthetic encoded payloads and no provider calls."""

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.core.image_budget import image_input_bytes
from reachy_brain.integrations.registry import (
    ActionPolicy,
    Connection,
    Rule,
    Tool,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.vision.store import VisualStore


def image(size):
    return {"type": "input_image", "image_url": "data:image/jpeg;base64," + "A" * size}


@pytest.mark.features("V2", "V4", "V6", "E1")
@pytest.mark.scenario("MODEL-IMAGE-BYTES-BEFORE-REQUEST")
@pytest.mark.parametrize("initial_oversize", [False, True])
async def test_image_budget_prevents_initial_or_followup_model_dispatch(initial_oversize):
    calls, events = [], []
    registry, policy = ToolRegistry(), ActionPolicy()
    registry.add_connection(Connection("visual", "test"))

    async def attach(payload, context):
        context.attachments.append(image(600000))
        return {}

    tool = Tool(
        "visual", "test", "image", "Synthetic image", {"type": "object"}, {"type": "object"}, attach
    )
    registry.register(tool)
    policy.set(Rule(tool.key, "read", "allow"))

    class Brain:
        async def stream(self, messages, tools):
            calls.append(len(messages))
            yield {
                "type": "item",
                "item": {
                    "type": "function_call",
                    "name": tool.key,
                    "arguments": "{}",
                    "call_id": "synthetic",
                },
            }

    class Voice:
        async def stream(self, text):
            if False:
                yield b""

    async def send(message):
        events.append(message)

    core = Conversation(
        Settings(_env_file=None, model_image_max_mib=1),
        Brain(),
        {"openai": Voice()},
        ToolExecutor(registry, policy, None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.history.append(
        {"role": "user", "content": [image(1048576 if initial_oversize else 600000)]}
    )
    await core.user_turn("Inspect the synthetic image")
    await core.task
    assert len(calls) == (0 if initial_oversize else 1)
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "model_image_byte_limit"
    assert "configured image limit" in errors[0]["message"]
    assert "narrower time range" in errors[0]["message"]
    assert "crop" in errors[0]["message"]


@pytest.mark.features("V4", "V6", "E1")
@pytest.mark.scenario("MODEL-IMAGE-BUDGET-BOUNDARY")
def test_budget_counts_repeated_images_and_rejects_unbounded_references():
    item = image(40)
    size = len(item["image_url"])
    messages = [{"role": "user", "content": [item, item]}]
    assert image_input_bytes(messages, 2 * size) == 2 * size
    with pytest.raises(ToolError, match="model_image_byte_limit"):
        image_input_bytes(messages, 2 * size - 1)
    for invalid in (
        {"type": "input_image", "image_url": "https://example.invalid/image"},
        {"type": "input_image", "file_id": "unmeasured-file"},
    ):
        with pytest.raises(ToolError, match="unbounded_model_image"):
            image_input_bytes([invalid], 1000000)
