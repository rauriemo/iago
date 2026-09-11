"""Bounded metadata migration/retention with synthetic session and gesture IDs."""

import json
import sqlite3
from contextlib import closing

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.storage.transcripts import Transcripts


@pytest.mark.features("C9", "P10")
@pytest.mark.scenario("TRANSCRIPT-METADATA-MIGRATION-BOUNDS")
def test_migration_and_metadata_share_retention_and_capacity(tmp_path):
    path = tmp_path / "transcripts.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE entries(session TEXT,entry TEXT,role TEXT,text TEXT,kind TEXT,created REAL,PRIMARY KEY(session,entry))"
        )
        db.execute("INSERT INTO entries VALUES('old','u','user','Legacy','typed',1)")
    store = Transcripts(path, max_bytes=1024)
    assert store.entries("old")[0]["metadata"] == {}
    generation = store.set_enabled(True)["generation"]
    metadata = {
        "epoch": 2,
        "profile": "desktop",
        "mode": "conversation",
        "session_started": 1,
        "source_id": "camera",
        "source_generation": 3,
        "question_id": "question",
        "event_id": "gesture",
        "capture_start": 2.0,
        "capture_end": 2.4,
    }
    assert store.record(
        "s", "g", "user", "yes", "gesture", generation=generation, metadata=metadata
    )
    assert store.entries("s")[0]["metadata"] == metadata
    for invalid in [
        [],
        {"token": "synthetic-secret"},
        {"capture_start": 4, "capture_end": 2},
        {"capture_end": float("inf")},
    ]:
        with pytest.raises(ToolError, match="invalid_transcript_metadata"):
            store.record("s", "g", "user", "no", "gesture", generation=generation, metadata=invalid)
    with pytest.raises(ToolError, match="transcript_storage_limit"):
        store.record(
            "s", "g", "user", "x" * 900, "gesture", generation=generation, metadata=metadata
        )
    assert store.entries("s")[0]["text"] == "yes"
    store.delete("s")
    assert not store.record(
        "s", "g", "user", "late", "gesture", generation=generation, metadata=metadata
    )
    assert not store.entries("s")
    assert store.entries("old")[0]["text"] == "Legacy"


@pytest.mark.features("C9")
@pytest.mark.scenario("TRANSCRIPT-GENERATED-UNICODE-CAPACITY")
def test_generated_unicode_uses_utf8_budget(tmp_path):
    store = Transcripts(tmp_path / "unicode.sqlite")
    generation = store.set_enabled(True)["generation"]
    generated = "\U0001f600" * 12000
    assert store.record(
        "s",
        "a",
        "assistant",
        "",
        "heard",
        generation=generation,
        metadata={"generated_text": generated, "interrupted": True},
    )
    assert store.entries("s")[0]["metadata"]["generated_text"] == generated


@pytest.mark.features("C9", "D5")
@pytest.mark.scenario("TRANSCRIPT-CAPACITY-DIAGNOSTICS")
def test_diagnostics_charge_metadata_updates_and_deletion(tmp_path):
    metadata = {"generated_text": "\U0001f600" * 20, "interrupted": True}
    expected = len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    store = Transcripts(tmp_path / "capacity.sqlite", max_bytes=expected + 3, max_entries=10)
    generation = store.set_enabled(True)["generation"]
    store.record("s", "a", "assistant", "yes", "heard", generation=generation, metadata=metadata)
    state = store.state()
    assert state["text_bytes"] == 3
    assert state["metadata_bytes"] == expected
    assert state["payload_bytes"] == state["max_payload_bytes"]
    assert state["at_capacity"]
    assert state["database_bytes"] >= state["payload_bytes"]
    with pytest.raises(ToolError, match="transcript_storage_limit"):
        store.record("s", "b", "user", "x", "typed", generation=generation)
    assert store.state()["payload_bytes"] == expected + 3
    store.record("s", "a", "assistant", "yes", "heard", generation=generation)
    assert store.state()["payload_bytes"] == 3
    assert not store.state()["at_capacity"]
    deleted = store.delete()
    assert deleted["payload_bytes"] == deleted["metadata_bytes"] == deleted["entries"] == 0
    assert deleted["database_bytes"] > 0  # Empty database pages are not transcript payload.
