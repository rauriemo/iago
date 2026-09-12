"""Actual app startup and background refresh of a synthetic legacy project index."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.knowledge.index import ProjectIndex
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "E1", "D5")
@pytest.mark.scenario("INDEX-UPGRADE-APPLICATION-STARTUP")
def test_startup_rebuilds_legacy_index_without_explicit_refresh(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    originals = {
        "brief.txt": b"Synthetic copper optics.",
        "credentials.txt": b'"api_key": "SYNTHETIC_UPGRADE_CREDENTIAL"',
    }
    for name, content in originals.items():
        (root / name).write_bytes(content)
    data = tmp_path / "private"
    legacy = ProjectIndex(data / "projects.sqlite")
    project = legacy.add_project("Keep project", root)
    legacy._parse = lambda path: {
        "status": "indexed",
        "passages": [
            {"locator_kind": "lines", "locator": "1", "offset": 0, "text": path.read_text()}
        ],
    }
    legacy.reindex(project)
    with legacy.database() as db:
        db.execute("PRAGMA user_version=0")
    app = create_app(Settings(_env_file=None, data_dir=data), token="synthetic-control")
    index = app.state.projects
    parse = index._parse
    entered, release = threading.Event(), threading.Event()

    def held(path):
        entered.set()
        assert release.wait(8), "background extraction was not released"
        return parse(path)

    index._parse = held
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer synthetic-control"
        try:
            assert entered.wait(3), "startup did not schedule ordinary refresh"
            status = client.get("/api/status")
            assert status.status_code == 200
            assert "SYNTHETIC_UPGRADE_CREDENTIAL" not in status.text
            assert index.status(project)["files_total"] == 0
            assert (
                client.post(
                    "/api/projects", json={"action": "activate", "project": project}
                ).status_code
                == 200
            )
            query = {
                "action": "search",
                "project": project,
                "query": "SYNTHETIC_UPGRADE_CREDENTIAL",
            }
            assert client.post("/api/projects", json=query).json()["passages"] == []
        finally:
            release.set()
        deadline = time.monotonic() + 8
        while index.status(project)["coverage"] != {"indexed": 1, "excluded": 1}:
            assert time.monotonic() < deadline, index.status(project)
            time.sleep(0.02)
        assert client.post("/api/projects", json=query).json()["passages"] == []
        result = client.post("/api/projects", json={**query, "query": "copper"}).json()
        assert len(result["passages"]) == 1 and "copper" in result["passages"][0]["text"]
    for name, content in originals.items():
        assert (root / name).read_bytes() == content
