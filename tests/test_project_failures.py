"""Actual indexing failures on synthetic folders; errors stay bounded and recoverable."""

import sqlite3

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-SCAN-FAILURE-STATUS-RECOVERY")
def test_whole_scan_failure_is_visible_without_partial_index_and_clears_on_success(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    first, extra = root / "first.txt", root / "extra.txt"
    first.write_text("Synthetic copper optics.")
    extra.write_text("Synthetic extra file.")
    index = ProjectIndex(tmp_path / "index.sqlite", max_files=1)
    project = index.add_project("Synthetic", root)
    with pytest.raises(ToolError, match="file_limit"):
        index.reindex(project)
    status = index.status(project)
    assert status["error"] == "file_limit" and status["files_total"] == 0
    assert not status["indexing"]
    # A coalesced no-op must not clear the error from the last actual scan.
    index.refreshing.add(project)
    index.reindex(project)
    assert index.status(project)["error"] == "file_limit"
    index.refreshing.remove(project)
    extra.unlink()
    index.reindex(project, force=True)
    assert index.status(project)["error"] is None
    assert index.status(project)["coverage"] == {"indexed": 1}
    assert first.read_text() == "Synthetic copper optics."


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-SCAN-ERROR-REDACTION-REMOVAL")
@pytest.mark.parametrize(
    "failure,code", [(OSError, "index_failed"), (sqlite3.OperationalError, "index_storage_error")]
)
def test_private_exception_text_is_not_status_and_removed_project_leaves_no_error(
    tmp_path, failure, code
):
    root = tmp_path / "originals"
    root.mkdir()
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Synthetic", root)
    original = index._reindex

    def fail(*args, **kwargs):
        raise failure("synthetic-private-credential-and-path")

    index._reindex = fail
    with pytest.raises(failure):
        index.reindex(project)
    status = index.status(project)
    assert status["error"] == code
    assert "synthetic-private" not in str(status)
    index.remove(project)
    assert not index.errors
    index._reindex = original
    with pytest.raises(ToolError, match="unknown_project"):
        index.reindex(project)
    assert not index.errors
