"""Synthetic archive sequence identity; not hardware frame counters."""

import time

import pytest
from fastapi.testclient import TestClient
from test_visual_store import image

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import ToolError
from reachy_brain.vision.store import VisualStore
from reachy_brain.web.app import create_app


@pytest.mark.features("V5", "V9", "D5")
@pytest.mark.scenario("SOURCE-FRAME-SEQUENCE-ISOLATION")
def test_sequence_is_source_generation_scoped_and_rejects_newer_duplicate_timestamp():
    store = VisualStore(clock=lambda: 100)
    a = store.source("synthetic", "camera", "Camera")
    b = store.source("synthetic", "screen", "Screen")
    first = store.add(a.id, 0, 95, image(), sequence=2)
    assert store.describe(first)["source_frame_id"] == "2"
    for seq in (2, 1):
        with pytest.raises(ToolError, match="stale_frame_sequence"):
            store.add(a.id, 0, 96, image(), sequence=seq)
    assert len(store.frames) == 1 and a.last_capture == 95
    store.add(b.id, 0, 96, image(), sequence=2)
    store.clear(a.id)
    store.add(a.id, 1, 97, image(), sequence=1)
    with pytest.raises(ToolError, match="stale_source"):
        store.add(a.id, 0, 98, image(), sequence=3)
    assert b.last_frame_sequence == 2


@pytest.mark.features("V5", "D5")
@pytest.mark.scenario("SOURCE-FRAME-SEQUENCE-BOUNDS")
@pytest.mark.parametrize("sequence", [True, -1, 1.5, "private", 9007199254740992])
def test_invalid_sequence_has_no_store_mutation(sequence):
    store = VisualStore(clock=lambda: 100)
    source = store.source("synthetic", "camera", "Camera")
    with pytest.raises(ToolError, match="stale_frame_sequence"):
        store.add(source.id, 0, 99, image(), sequence=sequence)
    assert not store.frames and source.last_frame_sequence == -1


@pytest.mark.features("V5", "V9", "D5")
@pytest.mark.scenario("SOURCE-FRAME-SEQUENCE-HTTP")
def test_duplicate_upload_is_rejected_before_decode(tmp_path, monkeypatch):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    visual = app.state.visual
    source = visual.source("synthetic", "camera", "Camera")
    calls = []

    def prepare(data):
        calls.append(data)
        return image()

    monkeypatch.setattr(visual, "prepare", prepare)
    with TestClient(app) as client:
        for index in range(2):
            response = client.post(
                f"/api/frame/{source.id}/0",
                content=b"synthetic",
                headers={
                    "Authorization": "Bearer test",
                    "X-Captured-At": str(time.time()),
                    "X-Frame-Sequence": "1",
                },
            )
            assert response.status_code == (200 if index == 0 else 409)
        assert len(calls) == 1 and len(visual.frames) == 1


@pytest.mark.features("V5", "C9")
@pytest.mark.scenario("SOURCE-FRAME-SEQUENCE-TRANSCRIPT")
def test_source_frame_identity_survives_saved_answer(tmp_path):
    from types import SimpleNamespace

    from reachy_brain.core.conversation import Conversation
    from reachy_brain.storage.transcripts import Transcripts

    visual = VisualStore(clock=lambda: 100)
    source = visual.source("synthetic", "camera", "Camera")
    frame = visual.add(source.id, 0, 99, image(), sequence=7)
    core = SimpleNamespace(answer_record_epoch=1, answer_metadata={})
    Conversation.record_evidence(core, 1, [visual.describe(frame)])
    store = Transcripts(tmp_path / "sequence.sqlite")
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
    ref = store.entries("session")[0]["metadata"]["evidence_refs"][0]
    assert ref["source_id"] == source.id and ref["source_generation"] == 0
    assert ref["source_frame_id"] == "7" and ref["id"] == frame.id
