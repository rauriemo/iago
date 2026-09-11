"""Real os.walk/SQLite with injected directory enumeration failures."""

import os
from pathlib import Path

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-INCOMPLETE-WALK-PRESERVES-INDEX")
@pytest.mark.parametrize(
    "failed_directory,operation",
    [("root", "enumerate"), ("nested", "enumerate"), ("nested", "resolve")],
)
def test_directory_failure_cannot_commit_false_deletions(
    tmp_path, monkeypatch, failed_directory, operation
):
    root = tmp_path / "originals"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "brief.txt").write_text("Synthetic copper optics.")
    (nested / "plan.txt").write_text("Synthetic cobalt lens.")
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Synthetic", root)
    index.activate(project)
    index.reindex(project)
    old = index.search(project, "cobalt")
    generation = index.status(project)["generation"]
    (root / "new.txt").write_text("Synthetic amber screen.")
    scandir = os.scandir
    resolve = Path.resolve
    denied = root if failed_directory == "root" else nested

    def fail(path):
        if Path(path) == denied:
            raise PermissionError("synthetic-private-directory-error")
        return scandir(path)

    def fail_resolve(path, *args, **kwargs):
        if path == denied:
            raise PermissionError("synthetic-private-directory-error")
        return resolve(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if operation == "enumerate":
            patch.setattr(os, "scandir", fail)
        else:
            patch.setattr(Path, "resolve", fail_resolve)
        with pytest.raises(ToolError, match="source_unavailable"):
            index.reindex(project)
    status = index.status(project)
    assert status["error"] == "source_unavailable"
    assert "synthetic-private" not in str(status)
    assert status["files_total"] == 2 and status["generation"] == generation
    assert index.search(project, "cobalt") == old
    assert not index.search(project, "amber")
    assert index.staging.used == 0 and not index.refreshing
    index.reindex(project)
    assert index.status(project)["error"] is None
    assert index.status(project)["files_total"] == 3
    assert index.search(project, "amber")
