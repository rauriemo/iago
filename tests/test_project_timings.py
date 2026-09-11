"""Real local operations and deterministic retention; no physical latency claims."""

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex
from reachy_brain.knowledge.timing import ProjectTimings
from reachy_brain.web.app import create_app


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-TIMINGS-OPERATIONS-ERRORS-NOOP")
def test_local_operations_record_timings_without_source_content(tmp_path):
    root = tmp_path / "private-originals"
    root.mkdir()
    (root / "secret-name.txt").write_text("Synthetic copper optics.")
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Private project", root)
    index.activate(project)
    index.reindex(project)
    hits = index.search(project, "copper")
    index.read(project, [hits[0]["id"]])
    with pytest.raises(ToolError):
        index.read(project, ["invalid-private-id"])
    index.refreshing.add(project)
    assert index.reindex(project) is False
    index.refreshing.remove(project)
    result = index.snapshot()["project_timings"]
    assert result["counts"] == {
        "refresh/ok": 1,
        "search/ok": 1,
        "read/ok": 1,
        "read/error": 1,
        "refresh/no_op": 1,
    }
    assert all(row["seconds"] >= 0 for row in result["samples"])
    assert all(
        set(row) == {"operation", "seconds", "outcome", "sequence"} for row in result["samples"]
    )
    for private in ("secret-name", "copper", "Private project", project, "invalid-private-id"):
        assert private not in str(result)


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-TIMINGS-BOUNDED-DETACHED-SNAPSHOT")
def test_retention_keeps_total_counts_and_detaches_export():
    timings = ProjectTimings(clock=lambda: 10)
    for _ in range(300):
        timings.record("search", 9.75, "ok")
    snapshot = timings.snapshot()
    assert len(snapshot["samples"]) == 256 and snapshot["counts"] == {"search/ok": 300}
    assert all(row["seconds"] == 0.25 for row in snapshot["samples"])
    assert [row["sequence"] for row in snapshot["samples"]] == list(range(45, 301))
    snapshot["samples"][0]["seconds"] = -1
    snapshot["counts"].clear()
    assert timings.snapshot()["samples"][0]["seconds"] == 0.25
    assert timings.snapshot()["counts"]["search/ok"] == 300


@pytest.mark.features("K1", "D4", "D5")
@pytest.mark.scenario("PROJECT-TIMINGS-AUTHENTICATED-EXPORT")
def test_timing_export_requires_authentication(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    app.state.projects.timings.record("search", app.state.projects.timings.clock(), "error")
    with TestClient(app) as client:
        assert client.get("/api/project-timings").status_code == 401
        result = client.get("/api/project-timings", headers={"Authorization": "Bearer test"})
        assert result.status_code == 200 and result.json()["counts"] == {"search/error": 1}
