"""Real local files growing after stat; no original-file edits by the indexer."""

import hashlib
from pathlib import Path

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1", "D6")
@pytest.mark.scenario("DOCUMENT-HASH-GROWTH-BOUND")
@pytest.mark.parametrize("operation", ["revision", "index"])
def test_growth_between_stat_and_hash_is_bounded_and_reported(tmp_path, monkeypatch, operation):
    root = tmp_path / "originals"
    root.mkdir()
    target = root / "growing.txt"
    target.write_bytes(b"small original")
    index = ProjectIndex(tmp_path / "private" / "index.sqlite")
    project = index.add_project("Synthetic", root)
    opened = Path.open
    grown = b"x" * (20 * 1024 * 1024 + 1)

    def grow(path, mode="r", *args, **kwargs):
        if path == target and mode == "rb":
            with opened(target, "wb") as output:
                output.write(grown)
        return opened(path, mode, *args, **kwargs)

    def forbidden(path):
        raise AssertionError("oversized document reached extraction")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", grow)
        patch.setattr(index, "_parse", forbidden)
        if operation == "revision":
            with pytest.raises(ToolError, match="^over_limit$"):
                index._revision(target)
        else:
            assert index.reindex(project)
            assert index.status(project)["files"] == [
                {"relative": "growing.txt", "status": "over_limit"}
            ]
            index.activate(project)
            assert index.search(project, "small original") == []
    assert target.read_bytes() == grown


@pytest.mark.features("K1")
@pytest.mark.scenario("DOCUMENT-HASH-EXACT-LIMIT")
@pytest.mark.parametrize("size", [0, 20 * 1024 * 1024, 20 * 1024 * 1024 + 1])
def test_revision_preserves_digest_and_exact_file_limit(tmp_path, size):
    content = b"x" * size
    target = tmp_path / "bounded.txt"
    target.write_bytes(content)
    if size > 20 * 1024 * 1024:
        with pytest.raises(ToolError, match="^over_limit$"):
            ProjectIndex._revision(target)
    else:
        assert ProjectIndex._revision(target) == hashlib.sha256(content).hexdigest()
