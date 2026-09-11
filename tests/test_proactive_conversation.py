"""Real conversation/synthesis pipeline with synthetic model, voice and event evidence."""

import asyncio
import time

import pytest

from reachy_brain.behavior.engine import BehaviorEngine, Event
from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("P4", "P5", "P6", "P8", "C2")
@pytest.mark.scenario("PROACTIVE-PIPELINE-RECHECK")
@pytest.mark.parametrize("invalidate", [False, True])
async def test_greeting_uses_pipeline_but_disabled_rule_cannot_play(invalidate):
    entered, release = asyncio.Event(), asyncio.Event()
    observed, emitted = [], []

    class Brain:
        async def stream(self, messages, tools):
            observed.extend(messages)
            entered.set()
            await release.wait()
            yield {"type": "text", "text": "Hello there."}

    class Voice:
        async def stream(self, text):
            yield bytes(1920)

    async def send(message):
        emitted.append(message)

    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {"openai": Voice()},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    source = core.visual.source(core.connection, "camera", "Synthetic camera")
    engine = BehaviorEngine()
    engine.mode = "conversation"
    engine.rules["wave"].enabled = True
    event = Event("wave1", source.id, "wave_detected", time.time(), 0.95, duration=1)
    assert engine.offer(event, now=time.time()) == "queued"
    intent = engine.take(now=time.time())
    core.on_proactive_stop = lambda: engine.interrupt(now=time.time())
    assert await core.proactive(intent, lambda: engine.recheck(intent, now=time.time()))
    await entered.wait()
    if invalidate:
        engine.rules["wave"].enabled = False
    release.set()
    await asyncio.wait_for(core.task, 2)
    assert any("Observed event evidence" in str(m) for m in observed)
    assert not core.history  # A detection is never recorded as a real user utterance.
    assert any(m["type"] == "audio" for m in emitted) is (not invalidate)
    await core.speech_onset(time.time())
    assert core.user_speaking and core.proactive_guard is None
    assert engine.backoff_until > time.time()


@pytest.mark.features("P4", "P5", "V3", "V7")
@pytest.mark.scenario("PROACTIVE-EXACT-IMAGE-EVIDENCE")
@pytest.mark.parametrize("evicted", [False, True])
async def test_event_image_is_not_replaced_by_newer_camera_frame(evicted):
    import io

    from PIL import Image

    messages, emitted = [], []

    class Brain:
        async def stream(self, context, tools):
            messages.extend(context)
            yield {"type": "text", "text": "Hello."}

    class Voice:
        async def stream(self, text):
            yield bytes(960)

    async def send(message):
        emitted.append(message)

    store = VisualStore()
    source = store.source("owner", "camera", "Synthetic camera")
    frame_ids = []
    for index, color in enumerate(["red", "green"]):
        image = io.BytesIO()
        Image.new("RGB", (16, 16), color).save(image, "JPEG")
        frame_ids.append(
            store.add(
                source.id, 0, time.time() - 1 + index * 0.1, store.prepare(image.getvalue())
            ).id
        )
    old_image = store.image_input(frame_ids[0])
    if evicted:
        del store.frames[frame_ids[0]]
    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {"openai": Voice()},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        store,
        send,
    )
    core.mode = "conversation"
    intent = {
        "prompt": "Greet briefly",
        "evidence": {"source": source.id, "frames": [frame_ids[0]]},
    }
    assert await core.proactive(intent, lambda: True)
    await asyncio.wait_for(core.task, 2)
    displayed = next(m["frames"] for m in emitted if m["type"] == "evidence")
    assert [frame["id"] for frame in displayed] == ([] if evicted else [frame_ids[0]])
    images = [
        part
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "input_image"
    ]
    assert images == ([] if evicted else [old_image])
    assert not any(frame_ids[1] in str(message) for message in messages)
    if evicted:
        assert any("no longer available" in str(message) for message in messages)
    await core.stop()
