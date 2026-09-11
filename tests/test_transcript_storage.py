"""Synthetic text verifies opt-in, bounded updates and deletion-generation protection."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.storage.transcripts import Transcripts


@pytest.mark.features("C9", "C8", "D5")
@pytest.mark.scenario("TRANSCRIPT-OPT-IN-DELETE-GENERATION")
def test_disabled_default_and_delete_rejects_late_queued_text(tmp_path):
    store = Transcripts(tmp_path / "transcripts.sqlite")
    assert not store.state()["enabled"]
    assert not store.record("s", "u1", "user", "Private unsaved idea", "typed", generation=0)
    assert store.sessions() == []
    generation = store.set_enabled(True)["generation"]
    assert store.set_enabled(True)["generation"] == generation
    assert store.record("s", "u1", "user", "Saved idea", "typed", generation=generation)
    assert store.record("other", "u1", "user", "Keep this", "speech", generation=generation)
    next_state = store.delete("s")
    assert next_state["generation"] > generation
    assert not store.record(
        "s", "a1", "assistant", "Late old answer", "heard", generation=generation
    )
    assert not store.entries("s") and store.entries("other")[0]["text"] == "Keep this"
    disabled = store.set_enabled(False)
    assert not store.record(
        "other", "u2", "user", "Unrecorded", "speech", generation=disabled["generation"]
    )
    assert len(store.entries("other")) == 1


@pytest.mark.features("C9", "C8", "D5")
@pytest.mark.scenario("TRANSCRIPT-BOUNDED-HEARD-UPSERT")
def test_heard_prefix_updates_one_entry_and_capacity_failure_preserves_it(tmp_path):
    store = Transcripts(tmp_path / "transcripts.sqlite", max_bytes=30, max_entries=1)
    generation = store.set_enabled(True)["generation"]
    assert store.record("s", "answer-1", "assistant", "First part.", "heard", generation=generation)
    assert store.record(
        "s", "answer-1", "assistant", "First part. Second part.", "heard", generation=generation
    )
    assert store.state()["entries"] == 1
    with pytest.raises(ToolError, match="transcript_storage_limit"):
        store.record("s", "answer-1", "assistant", "x" * 31, "heard", generation=generation)
    assert store.entries("s")[0]["text"] == "First part. Second part."
    with pytest.raises(ToolError, match="transcript_storage_limit"):
        store.record("s", "user-2", "user", "yes", "typed", generation=generation)
    assert store.delete()["entries"] == 0


@pytest.mark.features("C9", "D5")
@pytest.mark.scenario("TRANSCRIPT-CONCURRENT-CAPACITY-REOPEN")
def test_concurrent_capacity_and_persisted_opt_in(tmp_path):
    path = tmp_path / "transcripts.sqlite"
    store = Transcripts(path, max_entries=1)
    generation = store.set_enabled(True)["generation"]

    def save(entry):
        try:
            return store.record("s", entry, "user", "Saved", "typed", generation=generation)
        except ToolError as exc:
            assert exc.code == "transcript_storage_limit"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ["a", "b"])) == [False, True]
    reopened = Transcripts(path, max_entries=1)
    assert reopened.state()["enabled"]
    assert reopened.state()["entries"] == 1
    assert len(reopened.entries("s")) == 1
    disabled = reopened.set_enabled(False)
    reopened = Transcripts(path, max_entries=1)
    assert reopened.state() == disabled
    assert not reopened.record("s", "c", "user", "Late", "typed", generation=generation)
