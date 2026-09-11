"""Real index and HTTP scheduling with synthetic parser failure and controlled barriers."""

import threading

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.knowledge.index import ProjectIndex
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-EXPLICIT-REINDEX-RETRIES-UNCHANGED")
def test_background_skips_unchanged_failure_but_explicit_reindex_recovers(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    path = root / "brief.txt"
    text = "Synthetic project uses copper optics."
    path.write_text(text)
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Synthetic", root)
    parse = index._parse
    attempts = []

    def fail_once(path):
        attempts.append(path)
        return (
            {"status": "resource_limits_unavailable", "passages": []}
            if len(attempts) == 1
            else parse(path)
        )

    index._parse = fail_once
    index.reindex(project)
    assert index.status(project)["coverage"] == {"resource_limits_unavailable": 1}
    index.reindex(project)
    assert len(attempts) == 1
    index.reindex(project, force=True)
    assert len(attempts) == 2 and index.status(project)["coverage"] == {"indexed": 1}
    index.activate(project)
    assert "copper" in index.search(project, "copper")[0]["text"]
    assert path.read_text() == text


@pytest.mark.features("K1", "D4", "D5")
@pytest.mark.scenario("PROJECT-REINDEX-REQUEST-DURING-ACTIVE-WORK")
def test_repeated_explicit_requests_coalesce_without_being_lost(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    (root / "brief.txt").write_text("Synthetic copper optics.")
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), token="test")
    index = app.state.projects
    entered, release, forced_complete = threading.Event(), threading.Event(), threading.Event()
    parse, reindex = index._parse, index.reindex
    attempts = []

    def held(path):
        attempts.append(path)
        if len(attempts) == 1:
            entered.set()
            assert release.wait(8)
            return {"status": "timeout", "passages": []}
        return parse(path)

    def observe(*args, **kwargs):
        result = reindex(*args, **kwargs)
        if kwargs.get("force"):
            forced_complete.set()
        return result

    index._parse, index.reindex = held, observe
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test"
        project = client.post(
            "/api/projects", json={"action": "add", "name": "Synthetic", "root": str(root)}
        ).json()["project"]
        try:
            assert entered.wait(3)
            for _ in range(5):
                assert (
                    client.post(
                        "/api/projects", json={"action": "refresh", "project": project}
                    ).status_code
                    == 200
                )
        finally:
            release.set()
        assert forced_complete.wait(5), "Explicit reindex was lost while indexing was active"
        assert index.status(project)["coverage"] == {"indexed": 1}
        assert len(attempts) == 2
