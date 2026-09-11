"""Conversation provenance with synthetic images, a fake evidence tool and fake speech."""

import asyncio
import io
import json
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from reachy_brain.behavior.engine import Event
from reachy_brain.behavior.thumbs import Observation, Question
from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import (
    ActionPolicy,
    Connection,
    Rule,
    Tool,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts
from reachy_brain.vision.evidence import EventEvidence
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C9")
@pytest.mark.scenario("TRANSCRIPT-EVIDENCE-BOUNDS-EPOCH")
def test_reference_bounds_and_stale_epoch():
    core = SimpleNamespace(answer_record_epoch=2, answer_metadata={})
    rows = [
        {"id": str(index), "source": "camera", "generation": 0, "image": "private"}
        for index in range(40)
    ]
    Conversation.record_evidence(core, 1, rows)
    assert core.answer_metadata == {}
    Conversation.record_evidence(
        core,
        2,
        [
            None,
            {"id": "bad", "project_id": "missing-revision"},
            {"id": "bad", "source": "camera", "generation": True},
        ]
        + rows
        + rows,
    )
    assert core.answer_metadata["evidence_truncated"] is True
    refs = core.answer_metadata["evidence_refs"]
    assert len(refs) == 32
    assert [ref["id"] for ref in refs] == [str(index) for index in range(32)]
    assert "private" not in json.dumps(refs)


@pytest.mark.features("C9", "V3", "K1", "P7")
@pytest.mark.scenario("TRANSCRIPT-EVIDENCE-TRIGGER-REFERENCES")
async def test_trigger_and_evidence_keep_only_bounded_references(tmp_path):
    class Brain:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield {
                    "type": "item",
                    "item": {
                        "type": "function_call",
                        "call_id": "lookup",
                        "name": "fixture__local__lookup",
                        "arguments": "{}",
                    },
                }
            else:
                yield {"type": "text", "text": "Synthetic answer."}

    class Voice:
        async def stream(self, text):
            yield bytes(1920)

    registry, policy = ToolRegistry(), ActionPolicy()
    registry.add_connection(Connection("fixture", "local"))
    passage = {
        "id": "passage",
        "project_id": "project",
        "revision": "revision",
        "text": "Private synthetic source text must not be duplicated into metadata.",
    }

    async def lookup(payload, context):
        context.evidence.extend([passage, passage])
        return {"passages": [passage]}

    tool = Tool(
        "fixture",
        "local",
        "lookup",
        "Fake evidence",
        {"type": "object"},
        {"type": "object"},
        lookup,
    )
    registry.register(tool)
    policy.set(Rule(tool.key, "read", "allow"))
    visual = VisualStore()
    source = visual.source("fixture", "camera", "Synthetic camera")
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(buffer, "JPEG")
    frame = visual.add(source.id, source.generation, time.time(), visual.prepare(buffer.getvalue()))
    store = Transcripts(tmp_path / "transcripts.sqlite")
    recorder = TranscriptRecorder(store)
    await recorder.start()
    await recorder.change("enabled", True)

    async def send(message):
        if message["type"] == "segment_end":
            await core.heard(message["epoch"], message["segment"])

    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {"openai": Voice()},
        ToolExecutor(registry, policy, None),
        visual,
        send,
    )
    core.mode = "conversation"
    core.record, core.record_snapshot = recorder.submit, recorder.snapshot
    try:
        assert await core.proactive(
            {
                "rule": "test-rule",
                "prompt": "Synthetic prompt",
                "evidence": {"id": "test-event", "source": source.id, "frames": [frame.id]},
            },
            lambda: True,
        )
        await core.task
        await core.stop()
        await recorder.queue.join()
        row = (await asyncio.to_thread(store.entries, core.session))[0]
        metadata = row["metadata"]
        assert metadata["trigger_rule_id"] == "test-rule"
        assert metadata["trigger_event_id"] == "test-event"
        assert metadata["evidence_refs"] == [
            {"kind": "visual", "id": frame.id, "source_id": source.id, "source_generation": 0},
            {"kind": "document", "id": "passage", "project_id": "project", "revision": "revision"},
        ]
        assert passage["text"] not in json.dumps(metadata)
        assert "data:image" not in json.dumps(metadata)
        visual.clear()
        assert not visual.frames
        assert (await asyncio.to_thread(store.entries, core.session))[0]["metadata"] == metadata
    finally:
        await recorder.close()


@pytest.mark.features("C9", "P10", "V3")
@pytest.mark.scenario("THUMBS-SAVED-FRAME-EVENT-PROVENANCE")
async def test_saved_gesture_retains_bounded_provenance_not_images(tmp_path):
    store = Transcripts(tmp_path / "gesture.sqlite")
    recorder = TranscriptRecorder(store)
    await recorder.start()
    await recorder.change("enabled", True)

    async def send(message):
        pass

    async def answer(epoch):
        pass

    visual = VisualStore()
    source = visual.source("fixture", "camera", "Synthetic")
    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        visual,
        send,
    )
    core.record, core.record_snapshot = recorder.submit, recorder.snapshot
    core.mode = "conversation"
    core.answer = answer
    core.thumbs.enabled = True
    now = time.time()
    core.thumbs.present(Question("question", core.session, core.epoch, source.id, 0, now, now + 15))
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(buffer, "JPEG")
    support = await EventEvidence(visual).attach(
        [Event("candidate", source.id, "thumb_observation", now + 0.4, 0.95)], buffer.getvalue()
    )
    frames = support[0].frames
    try:
        for index, at in enumerate((0, 0.31, 0.4, 0.8)):
            core.thumbs.observe(
                Observation(
                    str(index),
                    source.id,
                    0,
                    "camera",
                    now + at,
                    "neutral" if at < 0.4 else "thumb_up",
                    0.95,
                    1,
                    True,
                    frames=frames if at >= 0.4 else (),
                    detector="synthetic-v1",
                ),
                now=now + at,
            )
        response = core.thumbs.poll(now=now + 1.06)
        assert response
        await core.accept_thumb(response)
        await core.task
        await recorder.queue.join()
        row = store.entries(core.session)[0]
        assert row["text"] == "yes"
        assert row["metadata"]["gesture_frame_ids"] == list(frames)
        assert row["metadata"]["gesture_event_ids"] == ["2", "3"]
        assert row["metadata"]["detector_version"] == "synthetic-v1"
        assert row["metadata"]["question_id"] == "question"
        visual.clear()
        assert not visual.frames
        assert store.entries(core.session)[0] == row
    finally:
        await recorder.close()
