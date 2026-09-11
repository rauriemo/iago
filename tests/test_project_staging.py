"""Real temporary files and SQLite with synthetic parsed content."""

import json
import weakref

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex
from reachy_brain.knowledge.staging import ScanStage, StagingBudget


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-STAGING-SHARED-BOUND-CLEANUP")
def test_shared_budget_exact_boundary_and_cleanup_on_failure(tmp_path):
    record = ["synthetic", "界"]
    size = len(json.dumps(record, separators=(",", ":")).encode()) + 1
    budget = StagingBudget(tmp_path, size * 2)
    with budget.open() as first:
        first.append(record)
        with pytest.raises(ToolError, match="index_staging_capacity"):
            with budget.open() as second:
                second.append(record)
                assert budget.used == size * 2
                second.append(record)
        assert second.file.closed and budget.used == size
        assert list(first) == [record]
        first.append(record)
        assert list(first) == [record, record]
    assert first.file.closed and budget.used == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-STAGING-RELEASES-PARSED-CONTENT")
def test_scan_does_not_retain_previous_parsed_files(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    for i in range(12):
        (root / f"{i}.txt").write_text("Synthetic original.")
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Synthetic", root)
    references, staged_sizes = [], []

    class Result(dict):
        pass

    def parse(path):
        assert sum(ref() is not None for ref in references) <= 1
        staged_sizes.append(index.staging.used)
        result = Result(
            status="indexed",
            passages=[
                {
                    "text": "marker " + "x" * 1490,
                    "locator_kind": "lines",
                    "locator": str(i),
                    "offset": 0,
                }
                for i in range(32)
            ],
        )
        references.append(weakref.ref(result))
        return result

    index._parse = parse
    index.reindex(project)
    assert len(references) == 12 and all(ref() is None for ref in references)
    assert staged_sizes[-1] > 500_000 and index.staging.used == 0
    assert index.status(project)["coverage"] == {"indexed": 12}
    index.activate(project)
    assert index.search(project, "marker")
    assert not list(tmp_path.glob(".iago-index-*"))


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-STAGING-FAILURE-RETRY")
def test_staging_overflow_preserves_index_and_releases_capacity_for_retry(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "brief.txt"
    source.write_text("Synthetic copper optics.")
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Synthetic", root)
    index.activate(project)
    index.reindex(project)
    before = index.search(project, "copper")
    index.staging.limit = 1
    with pytest.raises(ToolError, match="index_staging_capacity"):
        index.reindex(project, force=True)
    assert index.status(project)["error"] == "index_staging_capacity"
    assert index.search(project, "copper") == before
    assert index.staging.used == 0 and not index.refreshing
    assert not list(tmp_path.glob(".iago-index-*"))
    index.staging.limit = index.max_bytes
    index.reindex(project, force=True)
    assert index.status(project)["error"] is None


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-STAGING-COMMIT-CANCELLATION")
@pytest.mark.parametrize("files", [1, 2])
def test_cancellation_during_replay_rolls_back_prior_insert(tmp_path, monkeypatch, files):
    root = tmp_path / "originals"
    root.mkdir()
    for i in range(files):
        (root / f"{i}.txt").write_text("Synthetic copper optics.")
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Synthetic", root)
    replay = ScanStage.__iter__
    canceled = False
    replayed = []

    def interrupted(stage):
        nonlocal canceled
        for record in replay(stage):
            yield record
            replayed.append(record[0])
            canceled = True

    monkeypatch.setattr(ScanStage, "__iter__", interrupted)
    with pytest.raises(ToolError, match="canceled"):
        index.reindex(project, lambda: not canceled)
    assert len(replayed) == 1
    assert index.status(project)["files_total"] == 0
    with index.database() as db:
        for table in ("files", "passages", "search"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert index.staging.used == 0 and not index.refreshing
    assert not list(tmp_path.glob(".iago-index-*"))
