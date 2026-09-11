"""Real SQLite status aggregation on synthetic metadata; no parser quality claim."""

import pytest

from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-STATUS-COMPLETE-TOTALS-BOUNDED-LIST")
def test_totals_cover_all_project_files_while_details_stay_bounded(tmp_path):
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Synthetic large project", tmp_path)
    other = index.add_project("Other isolated project", tmp_path)
    with index.database() as db:
        db.executemany(
            "INSERT INTO files VALUES(?,?,?,?,?)",
            [
                (
                    str(n),
                    project,
                    f"{n:05d}.txt",
                    "synthetic-revision",
                    "indexed" if n < 1000 else "unreadable",
                )
                for n in range(1200)
            ],
        )
        db.execute(
            "INSERT INTO files VALUES(?,?,?,?,?)",
            ("other-file", other, "private.txt", "other-revision", "excluded"),
        )
    status = index.status(project)
    assert status["coverage"] == {"indexed": 1000, "unreadable": 200}
    assert status["files_total"] == 1200 and status["files_truncated"]
    assert len(status["files"]) == 1000
    assert all(row["status"] == "indexed" for row in status["files"])
    assert index.status(other)["coverage"] == {"excluded": 1}
    with index.database() as db:
        db.execute("DELETE FROM files WHERE project=? AND status='unreadable'", (project,))
    status = index.status(project)
    assert status["files_total"] == 1000 and not status["files_truncated"]
