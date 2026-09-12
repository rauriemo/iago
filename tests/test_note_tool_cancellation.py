"""Real registry/journal/note storage with a held local save; no physical audio claim."""

import asyncio
import threading

import pytest

from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Rule,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.storage.notes import Notes


@pytest.mark.features("C9", "C2", "E1", "D5")
@pytest.mark.scenario("NOTES-TOOL-NONBLOCKING-UNCERTAIN-WRITE")
@pytest.mark.parametrize("ending", ["success", "cancel", "timeout"])
async def test_held_note_save_preserves_loop_progress_and_duplicate_protection(
    tmp_path, monkeypatch, ending
):
    notes = Notes(tmp_path / "notes.sqlite")
    journal_path = tmp_path / "operations.sqlite"
    journal = OperationStore(journal_path)
    registry, policy = ToolRegistry(), ActionPolicy()
    register_builtins(registry, policy, None, None, notes)
    executor = ToolExecutor(registry, policy, journal)
    key = "notes__local__save_idea"
    payload = {"text": "Synthetic note for cancellation evaluation."}
    context = CallContext("synthetic", 0, operation_id="synthetic-operation")
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original = notes.save
    calls = []

    def held(text, *, operation_id=None):
        calls.append(text)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "note save blocked event-loop progress"
        try:
            return original(text, operation_id=operation_id)
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(notes, "save", held)
    request = None
    try:
        denied = await executor.execute(
            key, payload, CallContext("synthetic", 0, operation_id="unapproved")
        )
        assert denied["status"] == "confirmation_required" and not calls
        policy.set(Rule(key, "write", "allow"))  # Explicit standing authorization for this fixture.
        registry.tools[key].timeout = 1 if ending == "timeout" else 10
        request = asyncio.create_task(executor.execute(key, payload, context))
        await asyncio.wait_for(entered.wait(), 2)
        assert not request.done() and notes.count() == 0
        if ending == "success":
            release.set()
            result = await request
            assert result["status"] == "ok"
            assert result["result"]["provider_ref"] == result["result"]["note_id"]
            expected = "succeeded"
        elif ending == "cancel":
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, 1)
            expected = "uncertain"
        else:
            result = await asyncio.wait_for(request, 2)
            assert result["status"] == "uncertain"
            expected = "uncertain"
        assert journal.get(context.operation_id)["status"] == expected
        duplicate = await executor.execute(key, payload, context)
        assert duplicate["duplicate"] and duplicate["status"] == expected
        assert len(calls) == 1
        if expected == "uncertain":
            pending = await executor.reconcile(context.operation_id, CallContext("synthetic", 1))
            assert pending["status"] == "uncertain" and notes.count() == 0
        release.set()
        await asyncio.wait_for(finished.wait(), 2)
        assert notes.count() == 1
        assert journal.get(context.operation_id)["status"] == expected
        if ending == "success":
            assert journal.get(context.operation_id)["provider_ref"] == notes.list()[0]["id"]
    finally:
        release.set()
        if request and not request.done():
            request.cancel()
        await executor.close()
        journal.close()
    reopened = OperationStore(journal_path)
    resumed = ToolExecutor(registry, policy, reopened)
    try:
        assert reopened.get(context.operation_id)["status"] == expected
        duplicate = await resumed.execute(key, payload, context)
        assert duplicate["duplicate"] and duplicate["status"] == expected
        assert len(calls) == 1 and notes.count() == 1
        resolved = await resumed.reconcile(context.operation_id, CallContext("synthetic", 2))
        assert resolved["status"] == "succeeded"
        assert resolved["provider_ref"] == notes.list()[0]["id"]
    finally:
        await resumed.close()
        reopened.close()
