"""Session lifecycle with actual SQLite/recorder and no provider or device calls."""

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C9", "D4", "V6")
@pytest.mark.scenario("SESSION-END-RESTART-PERSISTENCE")
async def test_session_end_restart_and_late_answer_ownership(tmp_path):
    store = Transcripts(tmp_path / "transcripts.sqlite")
    recorder = TranscriptRecorder(store)
    await recorder.start()

    async def send(message):
        pass

    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.record, core.record_snapshot = recorder.submit, recorder.snapshot
    try:
        await core.set_mode("aware")
        await recorder.queue.join()
        assert store.sessions() == []
        await recorder.change("enabled", True)
        source = core.visual.source("test", "camera", "Synthetic")
        await core.set_mode("conversation")
        await recorder.queue.join()
        assert store.sessions()[0]["lifecycle"]["visual_generations"][source.id] == 0
        first = core.session
        core.answer_record_epoch = core.epoch
        core.answer_record_session = first
        core.answer_recording_token = recorder.snapshot()
        core.answer_generated = "Unheard synthetic answer"
        core.answer_metadata = core.transcript_metadata()
        old_epoch = core.epoch
        await core.set_mode("idle")
        ended = core.session_ended
        await core.set_mode("aware")
        second = core.session
        assert second != first
        # A canceled answer's finally block can arrive after a fresh session starts.
        core.record_answer(old_epoch)
        await recorder.queue.join()
        sessions = {row["session"]: row for row in store.sessions()}
        lifecycle = sessions[first]["lifecycle"]
        assert lifecycle["session_ended"] == ended
        assert lifecycle["session_started"] <= ended
        assert lifecycle["mode"] == "idle"
        assert lifecycle["transcript_saving"] is True
        assert lifecycle["visual_context_generation"] == 1
        assert lifecycle["visual_generations"] == {}
        assert store.entries(first)[0]["metadata"]["generated_text"] == core.answer_generated
        assert store.entries(second) == []
        assert "session_ended" not in sessions[second]["lifecycle"]
        assert any(row["kind"] == "session" for row in store.entries(first, include_session=True))
        # Deletion invalidates admitted generation, including lifecycle records.
        generation = recorder.snapshot()
        await recorder.change("delete", first)
        assert not store.record(
            first, "__session__", "system", "", "session", generation=generation, metadata=lifecycle
        )
        assert first not in {row["session"] for row in store.sessions()}
        await recorder.change("enabled", False)
        await core.set_mode("idle")
        await recorder.queue.join()
        assert "session_ended" not in store.sessions()[0]["lifecycle"]
    finally:
        await recorder.close()
