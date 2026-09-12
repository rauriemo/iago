"""Synthetic file mutation around real extraction, followed by ordinary refresh."""

import pytest

from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D6")
@pytest.mark.scenario("DOCUMENT-POST-EXTRACTION-RECOVERY")
@pytest.mark.parametrize("failure", ["growth", "removed", "read_error"])
def test_restoring_original_bytes_retries_failed_post_extraction_verification(tmp_path, failure):
    root = tmp_path / "originals"
    root.mkdir()
    path = root / "brief.txt"
    original = b"Synthetic copper optics project."
    path.write_bytes(original)
    index = ProjectIndex(tmp_path / "private" / "index.sqlite")
    project = index.add_project("Synthetic", root)
    index.activate(project)
    parse = index._parse
    revision = index._revision
    calls = []
    checks = []

    def fail_final_read_once(source):
        checks.append(source)
        if failure == "read_error" and len(checks) == 2:
            raise OSError("Synthetic final revision read failure")
        return revision(source)

    def mutate_after_extraction(source):
        result = parse(source)
        calls.append(source)
        if len(calls) == 1:
            if failure == "growth":
                source.write_bytes(b"x" * (20 * 1024 * 1024 + 1))
            elif failure == "removed":
                source.unlink()
        return result

    index._parse = mutate_after_extraction
    index._revision = fail_final_read_once
    assert index.reindex(project)
    assert index.search(project, "copper") == []
    path.write_bytes(original)
    assert index.reindex(project)
    assert len(calls) == 2
    assert index.status(project)["coverage"] == {"indexed": 1}
    assert "copper" in index.search(project, "copper")[0]["text"]
    assert path.read_bytes() == original
