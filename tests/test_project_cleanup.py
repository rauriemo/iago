"""Actual HTTP removal/SQLite with injected compaction failure only."""

import sqlite3
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "D4", "D5")
@pytest.mark.scenario("PROJECT-REMOVAL-CLEANUP-FAILURE-RETRY")
@pytest.mark.parametrize("failure", [OSError, sqlite3.OperationalError])
def test_committed_removal_reports_cleanup_failure_and_can_retry(tmp_path, monkeypatch, failure):
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "brief.txt"
    source.write_text("Synthetic copper optics.")
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), token="test")
    index = app.state.projects
    project = index.add_project("Synthetic", root)
    index.reindex(project)
    index.activate(project)
    database = index.database

    @contextmanager
    def fail_compaction():
        with database() as db:

            class Connection:
                def execute(self, sql, *args):
                    if sql == "VACUUM":
                        raise failure("synthetic-private-filesystem-error")
                    return db.execute(sql, *args)

            yield Connection()

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test"
        with monkeypatch.context() as patch:
            patch.setattr(index, "database", fail_compaction)
            response = client.post("/api/projects", json={"action": "remove", "project": project})
            assert response.status_code == 409
            assert response.json() == {"error": "project_removed_storage_cleanup_incomplete"}
            retry = client.post("/api/projects", json={"action": "compact"})
            assert retry.status_code == 409
            assert retry.json() == {"error": "index_storage_cleanup_failed"}
        assert index.active is None and index.projects() == []
        with index.database() as db:
            for table in ("projects", "files", "passages", "search"):
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert client.post("/api/projects", json={"action": "compact"}).status_code == 200
        assert client.get("/api/status").json()["active_project"] is None
        assert source.read_text() == "Synthetic copper optics."
