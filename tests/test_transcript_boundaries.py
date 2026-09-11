"""Synthetic provider events exercise real core/recorder/SQLite opt-in boundaries."""

import asyncio
import time

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts
from reachy_brain.vision.store import VisualStore


def core_for(recorder):
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
    core.mode = "conversation"
    return core


@pytest.mark.features("C9", "C8", "D5")
@pytest.mark.scenario("TRANSCRIPT-ANSWER-OPT-IN-BOUNDARY")
@pytest.mark.parametrize("change", ["enable", "toggle", "delete"])
async def test_active_answer_cannot_cross_saving_generation(tmp_path, change):
    store = Transcripts(tmp_path / "text.sqlite")
    recorder = TranscriptRecorder(store)
    await recorder.start()
    if change != "enable":
        await recorder.change("enabled", True)
    core = core_for(recorder)
    started = asyncio.Event()

    class Speech:
        provider = "openai"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def answer(epoch, speech):
        core.ledger.add(epoch, "first", "Earlier prefix.")
        core.ledger.add(epoch, "later", "Later prefix.")
        started.set()
        await asyncio.Event().wait()

    core.make_speech = lambda epoch: Speech()
    core._answer = answer
    core.task = asyncio.create_task(core.answer(core.epoch))
    try:
        await started.wait()
        await core.heard(core.epoch, "first")
        await recorder.queue.join()
        if change == "delete":
            await recorder.change("delete", core.session)
        elif change == "toggle":
            await recorder.change("enabled", False)
            await recorder.change("enabled", True)
        else:
            await recorder.change("enabled", True)
        await core.heard(core.epoch, "later")
        await recorder.queue.join()
        rows = await asyncio.to_thread(store.entries, core.session)
        assert [r["text"] for r in rows] == (["Earlier prefix."] if change == "toggle" else [])
        old = core.task
        await core.stop()
        await old
        started.clear()
        core.task = asyncio.create_task(core.answer(core.epoch))
        await started.wait()
        await core.heard(core.epoch, "first")
        await recorder.queue.join()
        rows = await asyncio.to_thread(store.entries, core.session)
        assert rows[-1]["entry"] == f"answer-{core.epoch}"
    finally:
        await core.stop()
        await core.task
        await recorder.close()


@pytest.mark.features("C9", "C1", "D5")
@pytest.mark.scenario("TRANSCRIPT-REORDERED-INPUT-OPT-IN")
async def test_late_recognition_keeps_its_original_commit_generation(tmp_path):
    store = Transcripts(tmp_path / "text.sqlite")
    recorder = TranscriptRecorder(store)
    await recorder.start()
    core = core_for(recorder)

    class STT:
        async def commit(self):
            return True

    async def no_answer(epoch):
        pass

    core.answer = no_answer
    try:
        await core.speech_onset(time.time())
        await core.commit_recognition(STT())
        await recorder.change("enabled", True)
        capture_start = time.time()
        await core.speech_onset(capture_start)
        await core.commit_recognition(STT(), capture_end=capture_start + 1)
        for item in ("old", "new"):
            await core.transcription_event(
                {"type": "input_audio_buffer.committed", "item_id": item}
            )
        for item, text in (("new", "New opted-in speech"), ("old", "Old private speech")):
            await core.transcription_event(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": item,
                    "transcript": text,
                }
            )
        await recorder.queue.join()
        rows = await asyncio.to_thread(store.entries, core.session)
        assert [r["text"] for r in rows] == ["New opted-in speech"]
        assert rows[0]["metadata"]["capture_start"] == capture_start
        assert rows[0]["metadata"]["capture_end"] == capture_start + 1
        assert not core.recording_commits and not core.recording_inputs
    finally:
        await core.stop()
        if core.task:
            await asyncio.gather(core.task, return_exceptions=True)
        await recorder.close()
