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
@pytest.mark.scenario("MODEL-PROVENANCE-BOUNDS-EPOCH")
def test_model_provenance_bounds_epoch_and_no_extra_content():
    core = SimpleNamespace(answer_record_epoch=2, answer_metadata={})
    row = {"response_id": "r", "requested_model": "gpt-6-astra", "secret": "private"}
    Conversation.record_model_response(core, 1, row)
    assert core.answer_metadata == {}
    Conversation.record_model_response(core, 2, row)
    Conversation.record_model_response(core, 2, {**row, "reported_model": "reported"})
    assert core.answer_metadata["model_responses"] == [
        {"response_id": "r", "requested_model": "gpt-6-astra", "reported_model": "reported"}
    ]
    for i in range(40):
        Conversation.record_model_response(core, 2, {**row, "response_id": str(i)})
    assert len(core.answer_metadata["model_responses"]) == 32
    assert core.answer_metadata["model_responses_truncated"] is True
    assert "private" not in json.dumps(core.answer_metadata)


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
                    "provenance": {
                        "response_id": "tool-response",
                        "requested_model": "gpt-6-astra",
                    },
                    "item": {
                        "type": "function_call",
                        "call_id": "lookup",
                        "name": "fixture__local__lookup",
                        "arguments": "{}",
                    },
                }
            else:
                yield {
                    "type": "text",
                    "text": "Synthetic answer.",
                    "provenance": {
                        "response_id": "answer-response",
                        "requested_model": "gpt-6-astra",
                        "reported_model": "provider-reported-synthetic",
                    },
                }

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
        assert metadata["model_responses"] == [
            {"response_id": "tool-response", "requested_model": "gpt-6-astra"},
            {
                "response_id": "answer-response",
                "requested_model": "gpt-6-astra",
                "reported_model": "provider-reported-synthetic",
            },
        ]
        assert metadata["trigger_rule_id"] == "test-rule"
        assert metadata["trigger_event_id"] == "test-event"
        assert metadata["evidence_refs"] == [
            {
                "kind": "visual",
                "id": frame.id,
                "source_id": source.id,
                "source_generation": 0,
                "captured": frame.captured,
                "image_sha256": frame.image_sha256,
                "capture_time_known": True,
                "capture_uncertainty_seconds": None,
                "capture_interval": None,
                "source_kind": "camera",
            },
            {"kind": "document", "id": "passage", "project_id": "project", "revision": "revision"},
        ]
        assert passage["text"] not in json.dumps(metadata)
        assert "data:image" not in json.dumps(metadata)
        visual.clear()
        assert not visual.frames
        assert (await asyncio.to_thread(store.entries, core.session))[0]["metadata"] == metadata
    finally:
        await recorder.close()


@pytest.mark.features("C9", "V4", "V9")
@pytest.mark.scenario("TRANSCRIPT-VISUAL-CROP-PROVENANCE")
def test_saved_visual_overview_and_distinct_crops_preserve_provenance(tmp_path):
    core = SimpleNamespace(answer_record_epoch=1, answer_metadata={})
    base = {
        "id": "frame",
        "source": "screen",
        "generation": 2,
        "captured": 123.5,
        "capture_time_known": False,
        "source_kind": "screen",
    }
    rows = [base, {**base, "region": [1, 2, 30, 40]}, {**base, "region": [50, 2, 30, 40]}]
    Conversation.record_evidence(core, 1, rows + rows)
    refs = core.answer_metadata["evidence_refs"]
    assert len(refs) == 3
    assert "region" not in refs[0]
    assert refs[1]["region"] == [1, 2, 30, 40]
    assert refs[2]["region"] == [50, 2, 30, 40]
    assert all(r["captured"] == 123.5 and r["capture_time_known"] is False for r in refs)
    store = Transcripts(tmp_path / "crops.sqlite")
    state = store.set_enabled(True)
    # Use the store's generation rather than assuming a recording configuration epoch.
    store.record(
        "session",
        "answer",
        "assistant",
        "Synthetic answer",
        "heard",
        generation=state["generation"],
        metadata=core.answer_metadata,
    )
    assert store.entries("session")[0]["metadata"]["evidence_refs"] == refs


@pytest.mark.parametrize(
    "region", [[True, 0, 1, 1], [0, 0, 0, 1], [8192, 0, 1, 1], "private", [1, 2, 3]]
)
def test_invalid_visual_provenance_fields_are_not_persisted(region):
    core = SimpleNamespace(answer_record_epoch=1, answer_metadata={})
    Conversation.record_evidence(
        core,
        1,
        [
            {
                "id": "f",
                "source": "s",
                "generation": 0,
                "region": region,
                "captured": float("nan"),
                "capture_time_known": "yes",
                "source_kind": "private",
            }
        ],
    )
    assert core.answer_metadata["evidence_refs"] == [
        {"kind": "visual", "id": "f", "source_id": "s", "source_generation": 0}
    ]


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


@pytest.mark.features("C9", "V5", "V6")
@pytest.mark.scenario("TRANSCRIPT-VISUAL-TIMING-BOUND")
@pytest.mark.parametrize("bound,known", [(0.25, True), (0, True), (None, True), (0.25, False)])
def test_saved_evidence_retains_timing_bound_and_derives_interval(tmp_path, bound, known):
    core = SimpleNamespace(answer_record_epoch=1, answer_metadata={})
    row = {
        "id": "f",
        "source": "camera",
        "generation": 0,
        "captured": 100,
        "capture_time_known": known,
        "capture_uncertainty_seconds": bound,
        "capture_interval": [1, 2],
    }
    Conversation.record_evidence(core, 1, [row])
    ref = core.answer_metadata["evidence_refs"][0]
    assert ref["capture_uncertainty_seconds"] == bound
    assert ref["capture_interval"] == (
        [100 - bound, 100 + bound] if known and bound is not None else None
    )
    store = Transcripts(tmp_path / "timing.sqlite")
    state = store.set_enabled(True)
    store.record(
        "session",
        "answer",
        "assistant",
        "Synthetic",
        "heard",
        generation=state["generation"],
        metadata=core.answer_metadata,
    )
    assert store.entries("session")[0]["metadata"]["evidence_refs"] == [ref]


@pytest.mark.features("C9", "V5")
@pytest.mark.scenario("TRANSCRIPT-VISUAL-INVALID-TIMING-BOUND")
@pytest.mark.parametrize("bound", [-1, True, "private", float("inf"), float("nan")])
def test_invalid_timing_bound_is_explicitly_unknown(bound):
    core = SimpleNamespace(answer_record_epoch=1, answer_metadata={})
    Conversation.record_evidence(
        core,
        1,
        [
            {
                "id": "f",
                "source": "camera",
                "generation": 0,
                "captured": 100,
                "capture_time_known": True,
                "capture_uncertainty_seconds": bound,
            }
        ],
    )
    ref = core.answer_metadata["evidence_refs"][0]
    assert ref["capture_uncertainty_seconds"] is None and ref["capture_interval"] is None
    assert "private" not in json.dumps(ref)
