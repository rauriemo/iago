"""Queued transcript metadata must describe admission time, not later mutable state."""

import pytest

from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts


@pytest.mark.features("C9", "V4", "D5")
@pytest.mark.scenario("TRANSCRIPT-METADATA-ADMISSION-SNAPSHOT")
async def test_queued_metadata_is_independent_of_later_answer_mutations(tmp_path):
    store = Transcripts(tmp_path / "transcripts.sqlite")
    store.set_enabled(True)
    recorder = TranscriptRecorder(store)
    await recorder.start()
    metadata = {
        "model_responses": [{"response_id": "first", "requested_model": "gpt-6-astra"}],
        "visual_generations": {"camera": 1},
    }
    try:
        # No await between admission and mutation: worker cannot consume the queue yet.
        assert recorder.submit(
            "session", "answer", "assistant", "Heard.", "heard", metadata=metadata
        )
        metadata["model_responses"][0]["response_id"] = "later"
        metadata["model_responses"].append({"response_id": "extra", "requested_model": "other"})
        metadata["visual_generations"]["camera"] = 2
        await recorder.queue.join()
        saved = store.entries("session")[0]
        assert saved["metadata"] == {
            "model_responses": [{"response_id": "first", "requested_model": "gpt-6-astra"}],
            "visual_generations": {"camera": 1},
        }
        assert recorder.status()["dropped"] == 0
    finally:
        await recorder.close()
