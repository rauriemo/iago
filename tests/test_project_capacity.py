"""Actual SQLite capacity enforcement using synthetic indexed passages."""

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-SQLITE-CAPACITY-ROLLBACK")
def test_index_overflow_rolls_back_and_can_retry_smaller_content(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "brief.txt"
    source.write_text("Synthetic original.")
    path = tmp_path / "index.sqlite"
    limit = 65536
    index = ProjectIndex(path, max_bytes=limit)
    project = index.add_project("Synthetic", root)
    index.activate(project)
    index.reindex(project)
    before = index.search(project, "Synthetic")
    generation = index.status(project)["generation"]
    parse = index._parse
    # Parser output is smaller than remaining disk space, but SQLite stores both
    # passages and FTS content/indexes, whose page allocation must also be bounded.
    index._parse = lambda _: {
        "status": "indexed",
        "passages": [
            {
                "text": " ".join(f"word{j}" for j in range(200)),
                "locator_kind": "lines",
                "locator": str(i),
                "offset": 0,
            }
            for i in range(7)
        ],
    }
    with pytest.raises(ToolError, match="index_capacity"):
        index.reindex(project, force=True)
    assert path.stat().st_size <= limit
    assert index.status(project)["error"] == "index_capacity"
    assert index.status(project)["generation"] == generation
    assert index.search(project, "Synthetic") == before
    assert not index.search(project, "word10")
    index._parse = parse
    index.reindex(project, force=True)
    assert index.status(project)["error"] is None
    assert source.read_text() == "Synthetic original."


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-CAPACITY-EVERY-CONNECTION")
def test_reopened_connections_enforce_page_limit_and_release_after_failure(tmp_path):
    path = tmp_path / "index.sqlite"
    limit = 65537  # Rounded down to complete database pages.
    for index in (ProjectIndex(path, max_bytes=limit), ProjectIndex(path, max_bytes=limit)):
        with pytest.raises(ToolError, match="index_capacity"):
            with index.database() as db:
                for i in range(100):
                    db.execute(
                        "INSERT INTO passages VALUES(?,?,?,?,?,?,?,?)",
                        (str(i), "file", "project", "revision", "lines", "1", 0, "x" * 4096),
                    )
        assert path.stat().st_size <= limit
        with index.database() as db:
            assert db.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == 0
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-LOWERED-CAPACITY-PRESERVES-INDEX")
def test_lowered_limit_preserves_existing_database_and_releases_handle(tmp_path):
    path = tmp_path / "index.sqlite"
    ProjectIndex(path)
    before = path.read_bytes()
    with pytest.raises(ToolError, match="index_capacity"):
        ProjectIndex(path, max_bytes=len(before) - 4096)
    assert path.read_bytes() == before
    path.rename(path.with_name("retained.sqlite"))
