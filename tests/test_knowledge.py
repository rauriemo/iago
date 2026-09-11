"""Synthetic local documents; boundary and revision behavior, not model-quality evidence."""

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-INDEX-REVISIONS")
def test_revision_removal_and_project_isolation(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "plan.md").write_text("The amber engine uses violet fuel.")
    (b / "plan.md").write_text("The amber engine uses silver fuel.")
    index = ProjectIndex(tmp_path / "index.sqlite")
    pa, pb = index.add_project("A", a), index.add_project("B", b)
    index.reindex(pa)
    index.reindex(pb)
    index.activate(pa)
    hits = index.search(pa, "amber engine")
    assert hits and "violet" in hits[0]["text"]
    assert all(h["project_id"] == pa for h in hits)
    old_id = hits[0]["id"]
    (a / "plan.md").write_text("The amber engine uses green fuel.")
    with pytest.raises(ToolError, match="stale"):
        index.read(pa, [old_id])
    index.reindex(pa)
    assert "green" in index.search(pa, "amber")[0]["text"]
    with pytest.raises(ToolError, match="inactive_project"):
        index.search(pb, "silver")
    index.remove(pa)
    assert (a / "plan.md").exists()
    with pytest.raises(ToolError):
        index.search(pa, "amber")


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-ROOT-EXCLUSIONS")
def test_secret_and_outside_root_exclusions(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "readme.md").write_text("Rocket uses cobalt wings.")
    (root / ".env").write_text("TOKEN=SYNTHETIC_SECRET")
    (root / "private.txt").write_text('api_key = "SYNTHETIC_SECRET"')
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.txt").write_text("Dependency material")
    index = ProjectIndex(tmp_path / "index.sqlite")
    project = index.add_project("Test", root)
    index.reindex(project)
    index.activate(project)
    assert not index.search(project, "SYNTHETIC_SECRET")
    assert not index.search(project, "Dependency")
    assert index.search(project, "cobalt")
    statuses = index.status(project)
    assert any(f["status"] == "excluded" for f in statuses["files"])
