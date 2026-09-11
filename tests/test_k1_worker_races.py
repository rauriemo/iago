"""Held real parser/verification work checks project removal and selection generations."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("K1-LATE-INDEX-WORKER")
@pytest.mark.parametrize("operation", ["remove", "cancel"])
def test_late_parser_cannot_restore_removed_or_canceled_records(tmp_path, operation):
    root = tmp_path / "project"
    root.mkdir()
    path = root / "plan.txt"
    text = "The synthetic engine uses copper coils."
    path.write_text(text, encoding="utf-8")
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Test", root)
    entered, release, canceled = threading.Event(), threading.Event(), threading.Event()
    parse = index._parse

    def held(path):
        result = parse(path)
        entered.set()
        assert release.wait(5)
        return result

    index._parse = held
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(index.reindex, project, lambda: not canceled.is_set())
        try:
            assert entered.wait(3)
            if operation == "remove":
                index.remove(project)
            else:
                canceled.set()
        finally:
            release.set()
        with pytest.raises(ToolError, match="canceled"):
            worker.result(timeout=3)
    assert path.read_text(encoding="utf-8") == text
    with index.database() as db:
        for table in ("files", "passages", "search"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert not index.refreshing


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("K1-PROJECT-SELECTION-ABA")
@pytest.mark.parametrize("operation", ["search", "read"])
def test_search_rejects_switch_away_and_back_during_verification(tmp_path, operation):
    index = ProjectIndex(tmp_path / "index.sqlite")
    projects = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        (root / "plan.txt").write_text(f"The engine uses {name} coils.", encoding="utf-8")
        project = index.add_project(name, root)
        index.reindex(project)
        projects.append(project)
    index.activate(projects[0])
    citation = index.search(projects[0], "engine")[0]["id"]
    entered, release = threading.Event(), threading.Event()
    verify = index._verify

    def held(*args):
        result = verify(*args)
        entered.set()
        assert release.wait(5)
        return result

    index._verify = held
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(
            index.search if operation == "search" else index.read,
            projects[0],
            "engine" if operation == "search" else [citation],
        )
        try:
            assert entered.wait(2)
            index.activate(projects[1])
            index.activate(projects[0])
        finally:
            release.set()
        with pytest.raises(ToolError, match="stale_project"):
            worker.result(timeout=3)
