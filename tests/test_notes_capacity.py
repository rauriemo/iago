"""Actual SQLite page accounting and recovery with synthetic notes."""

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.storage.notes import Notes


@pytest.mark.features("C9", "D5")
@pytest.mark.scenario("NOTES-SQLITE-CAPACITY-RECOVERY")
def test_notes_bound_actual_pages_and_reuse_deleted_space(tmp_path):
    path = tmp_path / "notes.sqlite"
    limit = 32769
    notes = Notes(path, max_bytes=limit)
    saved = []
    for i in range(100):
        try:
            saved.append(notes.save(str(i) + "界" * 2000))
        except ToolError as exc:
            assert exc.code == "notes_storage_limit"
            break
        assert path.stat().st_size <= limit
    assert 0 < len(saved) < 100
    assert notes.count() == len(saved)
    kept = notes.export(saved[-1])
    notes.delete(saved[0])
    replacement = notes.save("Synthetic replacement")
    assert notes.export(replacement) == b"Synthetic replacement"
    if len(saved) > 1:
        assert notes.export(saved[-1]) == kept
    assert path.stat().st_size <= limit
    reopened = Notes(path, max_bytes=limit)
    assert reopened.count() == len(saved)
    with reopened.connection() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert (
            db.execute("PRAGMA max_page_count").fetchone()[0]
            * db.execute("PRAGMA page_size").fetchone()[0]
            <= limit
        )


@pytest.mark.features("C9", "D5")
@pytest.mark.scenario("NOTES-LOWERED-CAPACITY-PRESERVES-DATA")
def test_smaller_limit_does_not_modify_existing_notes_or_hold_handle(tmp_path):
    path = tmp_path / "notes.sqlite"
    notes = Notes(path)
    note = notes.save("Synthetic retained note")
    before = path.read_bytes()
    with pytest.raises(ToolError, match="notes_storage_limit"):
        Notes(path, max_bytes=len(before) - 4096)
    assert path.read_bytes() == before
    moved = path.with_name("retained.sqlite")
    path.rename(moved)
    assert Notes(moved).export(note) == b"Synthetic retained note"
