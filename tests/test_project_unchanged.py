"""Actual SQLite byte preservation for unchanged synthetic project scans."""

import pytest

from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D5")
@pytest.mark.scenario("PROJECT-UNCHANGED-SCAN-NO-DATABASE-WRITE")
@pytest.mark.parametrize("contents", ["empty", "supported", "excluded", "mixed"])
def test_unchanged_background_scans_preserve_database_bytes(tmp_path, contents):
    root = tmp_path / "originals"
    root.mkdir()
    if contents in {"supported", "mixed"}:
        (root / "brief.txt").write_text("Synthetic copper optics.")
    if contents in {"excluded", "mixed"}:
        (root / ".env.synthetic").write_text("DUMMY_PRIVATE_TEST_CONTENT")
    path = tmp_path / "index.sqlite"
    index = ProjectIndex(path)
    project = index.add_project("Synthetic", root)
    index.reindex(project)
    before = path.read_bytes()
    status = index.status(project)
    assert status["updated"] > 0
    for _ in range(3):
        index.reindex(project)
        assert path.read_bytes() == before
        assert index.status(project)["generation"] == status["generation"]
    # An explicit reparse and a real content change still commit normally.
    index.reindex(project, force=True)
    forced = index.status(project)
    assert forced["generation"] == status["generation"] + 1
    (root / "new.txt").write_text("Synthetic cobalt lens.")
    index.reindex(project)
    assert index.status(project)["generation"] == forced["generation"] + 1
    index.activate(project)
    assert index.search(project, "cobalt")
