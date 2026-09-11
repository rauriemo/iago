"""Real conversation/recorder flow with synthetic text, PCM and sink acknowledgments."""

import asyncio

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts
from reachy_brain.vision.store import VisualStore


@pytest.mark.features("C9", "C8", "C2")
@pytest.mark.scenario("TRANSCRIPT-GENERATED-HEARD-INTERRUPTED")
@pytest.mark.parametrize("heard_all", [False, True])
async def test_generated_text_never_becomes_unheard_history(tmp_path, heard_all):
    class Brain:
        async def stream(self, messages, tools):
            yield {"type": "text", "text": "First sentence. Second sentence."}

    class Voice:
        async def stream(self, text):
            yield bytes(1920)

    store = Transcripts(tmp_path / "transcripts.sqlite")
    recorder = TranscriptRecorder(store)
    await recorder.start()
    await recorder.change("enabled", True)
    segments = []

    async def send(message):
        if message["type"] == "segment_end":
            segments.append(message["segment"])
            if heard_all or len(segments) == 1:
                await core.heard(message["epoch"], message["segment"])

    core = Conversation(
        Settings(_env_file=None),
        Brain(),
        {"openai": Voice()},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.mode = "conversation"
    core.record, core.record_snapshot = recorder.submit, recorder.snapshot
    try:
        await core.answer(core.epoch)
        assert len(segments) == 2
        await core.stop()
        await recorder.queue.join()
        rows = await asyncio.to_thread(store.entries, core.session)
        assert len(rows) == 1
        heard = "First sentence. Second sentence." if heard_all else "First sentence."
        assert rows[0]["text"] == heard
        assert rows[0]["metadata"]["generated_text"] == "First sentence. Second sentence."
        assert rows[0]["metadata"]["interrupted"] is (not heard_all)
        assert list(core.history) == [{"role": "assistant", "content": heard}]
    finally:
        await recorder.close()
