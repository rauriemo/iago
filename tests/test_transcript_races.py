"""Real SQLite with deliberately held worker calls; synthetic text and gesture IDs."""

import asyncio
import threading

import pytest

from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts


@pytest.mark.features("C9", "P10", "D5")
@pytest.mark.scenario("TRANSCRIPT-CORRECTION-SATURATION")
async def test_gesture_correction_survives_full_queue_and_disabled_saving(tmp_path):
    store = Transcripts(tmp_path / "text.sqlite")
    recorder = TranscriptRecorder(store, capacity=2)
    await recorder.start()
    await recorder.change("enabled", True)
    assert recorder.submit("s", "g", "user", "yes", "gesture")
    await recorder.queue.join()
    entered, release = threading.Event(), threading.Event()
    original = store.record

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    store.record = held
    try:
        assert recorder.submit("s", "held", "user", "Held", "typed")
        assert await asyncio.to_thread(entered.wait, 1)
        assert recorder.submit("s", "a", "user", "Queued a", "typed")
        assert recorder.submit("s", "b", "user", "Queued b", "typed")
        await recorder.change("enabled", False)
        assert recorder.submit("s", "g", remove=True)
        assert recorder.queue.qsize() == 2
        assert recorder.dropped == 1
        release.set()
        await recorder.queue.join()
        assert not await asyncio.to_thread(store.entries, "s")
        assert not recorder.gestures
    finally:
        release.set()
        await recorder.close()


@pytest.mark.features("C9", "D5")
@pytest.mark.scenario("TRANSCRIPT-CANCELED-SETTING-FENCE")
async def test_canceled_setting_waits_for_actual_database_outcome(tmp_path):
    store = Transcripts(tmp_path / "text.sqlite")
    recorder = TranscriptRecorder(store)
    await recorder.start()
    entered, release = threading.Event(), threading.Event()
    original = store.set_enabled

    def held(value):
        entered.set()
        assert release.wait(3)
        return original(value)

    store.set_enabled = held
    pending = asyncio.create_task(recorder.change("enabled", True))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        assert recorder.paused
        assert not recorder.submit("s", "u", "user", "During unknown outcome", "typed")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not recorder.paused
        assert recorder.state == await asyncio.to_thread(store.state)
        assert recorder.state["enabled"]
        assert recorder.submit("s", "u", "user", "After enabled", "typed")
        await recorder.queue.join()
        assert len(await asyncio.to_thread(store.entries, "s")) == 1
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await recorder.close()
