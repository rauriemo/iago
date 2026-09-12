"""Persisted synthetic legacy extraction; preserve project roots and original bytes."""

import sqlite3

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "E1", "D5")
@pytest.mark.scenario("INDEX-EXTRACTION-UPGRADE")
def test_legacy_passages_retire_before_search_and_rebuild_on_ordinary_refresh(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "brief.txt"
    text = b'"api_key": "SYNTHETIC_LEGACY_CREDENTIAL"'
    source.write_bytes(text)
    path = tmp_path / "private" / "index.sqlite"
    index = ProjectIndex(path)
    project = index.add_project("Keep project", root)
    index.activate(project)
    index._parse = lambda _: {
        "status": "indexed",
        "passages": [{"locator_kind": "lines", "locator": "1", "offset": 0, "text": text.decode()}],
    }
    index.reindex(project)
    with index.database() as db:
        db.execute("PRAGMA user_version=0")
    reopened = ProjectIndex(path)
    reopened.activate(project)
    assert reopened.search(project, "SYNTHETIC_LEGACY_CREDENTIAL") == []
    assert reopened.status(project)["files_total"] == 0
    assert reopened.status(project)["updated"] == 0
    assert reopened.projects()[0]["name"] == "Keep project"
    assert reopened.reindex(project)
    assert reopened.status(project)["coverage"] == {"excluded": 1}
    assert source.read_bytes() == text
    # A current-format restart retains completed extraction rather than rebuilding each launch.
    current = ProjectIndex(path)
    assert current.status(project)["coverage"] == {"excluded": 1}
    assert current.status(project)["updated"] == reopened.status(project)["updated"]


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("INDEX-EXTRACTION-UPGRADE-FAILURE")
@pytest.mark.parametrize("failure", ["future_version", "delete_failure"])
def test_upgrade_failure_preserves_persisted_project_and_passages(tmp_path, failure):
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "brief.txt"
    source.write_text("Synthetic copper optics.")
    path = tmp_path / "index.sqlite"
    index = ProjectIndex(path)
    project = index.add_project("Keep project", root)
    index.reindex(project)
    version = 999 if failure == "future_version" else 0
    with index.database() as db:
        db.execute(f"PRAGMA user_version={version}")
        before = [tuple(r) for r in db.execute("SELECT * FROM passages")]
        if failure == "delete_failure":
            db.execute(
                "CREATE TRIGGER fail_upgrade BEFORE DELETE ON files BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
            )
    with pytest.raises(ToolError if failure == "future_version" else sqlite3.IntegrityError):
        ProjectIndex(path)
    with index.database() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        assert [tuple(r) for r in db.execute("SELECT * FROM passages")] == before
        assert db.execute("SELECT COUNT(*) FROM search").fetchone()[0] == len(before)
        assert (
            db.execute("SELECT name FROM projects WHERE id=?", (project,)).fetchone()[0]
            == "Keep project"
        )
    assert source.read_text() == "Synthetic copper optics."


@pytest.mark.features("K1", "D5", "D6")
@pytest.mark.scenario("INDEX-UPGRADE-UNAVAILABLE-SOURCE")
def test_unavailable_folder_after_upgrade_keeps_retired_content_unavailable(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "brief.txt"
    source.write_text("Synthetic copper optics.")
    path = tmp_path / "index.sqlite"
    index = ProjectIndex(path)
    project = index.add_project("Keep project", root)
    index.reindex(project)
    with index.database() as db:
        old_id = db.execute("SELECT id FROM passages").fetchone()[0]
        db.execute("PRAGMA user_version=0")
    unavailable = tmp_path / "disconnected-originals"
    # Both directory move targets are explicitly confined to this test's workspace.
    assert root.resolve().is_relative_to(tmp_path.resolve())
    assert unavailable.resolve().is_relative_to(tmp_path.resolve())
    root.rename(unavailable)
    try:
        reopened = ProjectIndex(path)
        reopened.activate(project)
        with pytest.raises(ToolError, match="^source_unavailable$"):
            reopened.reindex(project)
        assert reopened.status(project)["error"] == "source_unavailable"
        assert reopened.search(project, "copper") == []
        with pytest.raises(ToolError, match="^stale$"):
            reopened.read(project, [old_id])
        assert reopened.projects()[0]["root"] == str(root.resolve())
    finally:
        unavailable.rename(root)
    assert reopened.reindex(project)
    assert reopened.status(project)["error"] is None
    assert "copper" in reopened.search(project, "copper")[0]["text"]
    assert source.read_text() == "Synthetic copper optics."
