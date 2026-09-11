"""Twelve mutations of synthetic fixture files through real background indexing and HTTP."""

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.evals.k1_corpus import materialize
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "D1", "D5")
@pytest.mark.scenario("K1-CANDIDATE-TWELVE-BACKGROUND-MUTATIONS")
def test_twelve_changes_retire_citations_and_refresh_within_five_seconds(tmp_path, record_property):
    corpus = materialize(tmp_path / "corpus")
    baseline = {
        root / record["path"]: hashlib.sha256((root / record["path"]).read_bytes()).hexdigest()
        for name, root in corpus["projects"].items()
        for record in corpus["documents"]
        if record["project"] == name
    }
    changed, measured, projects = set(), [], {}
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    app = create_app(settings, token="test")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test"

        def action(kind, **kwargs):
            result = client.post("/api/projects", json={"action": kind, **kwargs})
            result.raise_for_status()
            return result.json()

        for name, root in corpus["projects"].items():
            projects[name] = action("add", name=name, root=str(root))["project"]
        deadline = time.monotonic() + 30
        while True:
            statuses = client.get("/api/status").json()["projects"]
            if len(statuses) == 3 and all(p["coverage"].get("indexed", 0) >= 10 for p in statuses):
                break
            assert time.monotonic() < deadline, statuses
            time.sleep(0.05)

        for operation in ("edit", "move", "delete", "create"):
            for name, root in corpus["projects"].items():
                project = projects[name]
                action("activate", project=project)
                filename, query = {
                    "edit": ("capacity.txt", "payload mass"),
                    "move": ("materials.txt", "enclosure material"),
                    "delete": ("schedule.md", "delivery date"),
                    "create": ("new-observation.txt", f"NEWOBSERVATION{name}"),
                }[operation]
                source = root / filename
                old = (
                    []
                    if operation == "create"
                    else action("search", project=project, query=query)["passages"]
                )
                if operation != "create":
                    old = [p for p in old if p["path"] == filename]
                    assert old
                target = source
                changed.add(source)
                if operation == "edit":
                    query = f"NEWMASS{name}"
                    source.write_text(
                        f"The payload mass limit is 99 kilograms. {query}\n", encoding="utf-8"
                    )
                elif operation == "move":
                    target = root / "moved-materials.txt"
                    assert source.resolve().is_relative_to(
                        root.resolve()
                    ) and target.resolve().is_relative_to(root.resolve())
                    source.rename(target)
                    changed.add(target)
                elif operation == "delete":
                    source.unlink()
                else:
                    source.write_text(f"{query}: the inspection lamp is amber.\n", encoding="utf-8")
                started = time.monotonic()
                for citation in old:
                    response = client.post(
                        "/api/projects",
                        json={"action": "read", "project": project, "ids": [citation["id"]]},
                    )
                    assert response.status_code == 409, response.text
                while True:
                    status = next(
                        p
                        for p in client.get("/api/status").json()["projects"]
                        if p["id"] == project
                    )
                    hits = action("search", project=project, query=query)["passages"]
                    assert all(hit["id"] not in {p["id"] for p in old} for hit in hits)
                    if operation == "delete":
                        ready = all(f["relative"] != filename for f in status["files"])
                    else:
                        ready = any(hit["path"] == target.name for hit in hits)
                    elapsed = time.monotonic() - started
                    if ready or elapsed >= 5:
                        measured.append(
                            {
                                "project": name,
                                "operation": operation,
                                "seconds": elapsed,
                                "observed": ready,
                                "within_target": ready and elapsed < 5,
                            }
                        )
                        record_property(
                            "measurements", {"mutations": list(measured), "target_seconds": 5}
                        )
                        record_property("sample_count", len(measured))
                        assert ready and elapsed < 5, measured
                        break
                    time.sleep(0.02)
        for path, digest in baseline.items():
            if path not in changed:
                assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert len(measured) == 12
    # Reopen the actual durable index without regenerating or reindexing fixture content manually.
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        assert len(client.get("/api/status").json()["projects"]) == 3
        for name, project in projects.items():
            client.post(
                "/api/projects", json={"action": "activate", "project": project}
            ).raise_for_status()
            result = client.post(
                "/api/projects",
                json={"action": "search", "project": project, "query": f"NEWOBSERVATION{name}"},
            )
            assert result.json()["passages"][0]["path"] == "new-observation.txt"
