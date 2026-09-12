"""Actual local note persistence and operation reconciliation; no external provider claims."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Rule,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.storage.notes import Notes


@pytest.mark.features("C9", "E1")
@pytest.mark.scenario("NOTES-IDEMPOTENT-LOCAL-PERSISTENCE")
def test_operation_bound_save_is_atomic_and_payload_conflicts_fail(tmp_path):
    notes = Notes(tmp_path / "notes.sqlite")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(
            pool.map(lambda _: notes.save("Synthetic idea", operation_id="op-one"), range(8))
        )
    assert len(set(ids)) == 1 and notes.count() == 1
    with pytest.raises(ToolError, match="note_operation_conflict"):
        notes.save("Different idea", operation_id="op-one")
    assert notes.export(ids[0]) == b"Synthetic idea"
    reopened = Notes(notes.path)
    assert reopened.identity == notes.identity
    assert reopened.operation_status("op-one") == {"status": "succeeded", "provider_ref": ids[0]}
    reopened.delete(ids[0])
    assert reopened.operation_status("op-one") == {"status": "uncertain"}


@pytest.mark.features("C9", "E1")
@pytest.mark.scenario("NOTES-RECONCILIATION-MIGRATION")
def test_existing_notes_survive_identity_initialization(tmp_path):
    path = tmp_path / "notes.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE notes(id TEXT PRIMARY KEY,text TEXT,created REAL)")
        db.execute("INSERT INTO notes VALUES('old', 'Synthetic old note', 1)")
    notes = Notes(path)
    assert notes.export("old") == b"Synthetic old note"
    assert notes.operation_status("pre-migration-operation") == {"status": "uncertain"}
    assert Notes(path).identity == notes.identity
    assert Notes(tmp_path / "replacement.sqlite").identity != notes.identity


@pytest.mark.features("C9", "E1")
@pytest.mark.scenario("NOTES-UNCERTAIN-RECONCILE-RESTART-ISOLATION")
@pytest.mark.parametrize("committed", [True, False])
async def test_lost_reply_reconciles_only_original_store_and_authorized_read(
    tmp_path, monkeypatch, committed
):
    path = tmp_path / "notes.sqlite"
    notes = Notes(path)
    journal_path = tmp_path / "journal.sqlite"
    key = "notes__local__save_idea"
    lookup = "notes__local__note_operation_status"
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, None, None, notes)
    policy.set(Rule(key, "write", "allow"))
    journal = OperationStore(journal_path)
    executor = ToolExecutor(registry, policy, journal)
    original = notes.save

    def lost_reply(text, *, operation_id=None):
        if committed:
            original(text, operation_id=operation_id)
        raise asyncio.CancelledError()

    monkeypatch.setattr(notes, "save", lost_reply)
    try:
        with pytest.raises(asyncio.CancelledError):
            await executor.execute(
                key, {"text": "Synthetic uncertain idea"}, CallContext("s", 0, operation_id="lost")
            )
        assert journal.get("lost")["status"] == "uncertain"
    finally:
        await executor.close()
        journal.close()
    journal = OperationStore(journal_path)
    reopened = Notes(path)
    for store in (Notes(tmp_path / "other.sqlite"), reopened):
        registry, policy = ToolRegistry(), ActionPolicy()
        register_builtins(registry, policy, None, None, store)
        resumed = ToolExecutor(registry, policy, journal)
        try:
            if store is not reopened:
                with pytest.raises(ToolError, match="reconciliation_unavailable"):
                    await resumed.reconcile("lost", CallContext("s", 1))
                continue
            policy.set(Rule(lookup, "read", "deny"))
            with pytest.raises(ToolError, match="reconciliation_not_authorized"):
                await resumed.reconcile("lost", CallContext("s", 1))
            policy.set(Rule(lookup, "read", "allow"))
            result = await resumed.reconcile("lost", CallContext("s", 1))
            assert result["status"] == ("succeeded" if committed else "uncertain")
            assert reopened.count() == int(committed)
            if committed:
                assert result["provider_ref"] == reopened.list()[0]["id"]
        finally:
            await resumed.close()
    journal.close()
