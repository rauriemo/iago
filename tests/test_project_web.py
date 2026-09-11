"""Authenticated HTTP project controls; synthetic files, no physical device claims."""

import time

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "E1", "C9")
@pytest.mark.scenario("WEB-PROJECT-CONTROLS")
def test_project_controls_and_notes(tmp_path):
    root = tmp_path / "originals"
    root.mkdir()
    original = root / "brief.txt"
    original.write_text("The synthetic Atlas launch uses a copper antenna.")
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data"), token="test")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test"
        added = client.post(
            "/api/projects", json={"action": "add", "name": "Atlas", "root": str(root)}
        )
        assert added.status_code == 200
        project = added.json()["project"]
        # Add schedules background indexing. A concurrent direct reindex returns
        # immediately when that worker already owns the project; it is not a barrier.
        deadline = time.monotonic() + 10
        while True:
            status = client.get("/api/status").json()["projects"]
            indexed = next(p for p in status if p["id"] == project)
            if indexed["files"]:
                break
            assert time.monotonic() < deadline, indexed
            time.sleep(0.02)
        assert (
            client.post(
                "/api/projects", json={"action": "search", "project": project, "query": "copper"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/projects", json={"action": "activate", "project": project}
            ).status_code
            == 200
        )
        results = client.post(
            "/api/projects", json={"action": "search", "project": project, "query": "copper"}
        ).json()["passages"]
        assert results and results[0]["path"] == "brief.txt"
        assert (
            client.post("/api/projects", json={"action": "remove", "project": project}).status_code
            == 200
        )
        assert original.read_text() == "The synthetic Atlas launch uses a copper antenna."
        assert client.get("/api/status").json()["projects"] == []
        note = client.post(
            "/api/notes", json={"action": "save", "text": "Keep this synthetic idea"}
        ).json()["id"]
        assert client.get("/api/notes").json()["notes"][0]["id"] == note
        assert client.post("/api/notes", json={"action": "delete", "id": note}).status_code == 200
        assert client.get("/api/notes").json()["notes"] == []
