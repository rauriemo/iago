"""Actual local skill files; deterministic growth between validation and opening."""

from pathlib import Path

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.skills.catalog import SkillCatalog


@pytest.mark.features("E1")
@pytest.mark.scenario("SKILL-RESOURCE-GROWTH-BOUND")
def test_growth_after_size_check_cannot_return_oversized_resource(tmp_path, monkeypatch):
    target = tmp_path / "SKILL.md"
    target.write_bytes(b"small")
    original = Path.open

    def growing(path, mode="r", *args, **kwargs):
        if path == target and mode in {"r", "rb"}:
            with original(target, "wb") as out:
                out.write(b"x" * 10000)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing)
    with pytest.raises(ToolError, match="invalid_skill_resource"):
        SkillCatalog._read(tmp_path, "SKILL.md", 32)


@pytest.mark.features("E1")
@pytest.mark.scenario("SKILL-RESOURCE-UTF8-BYTE-LIMIT")
def test_utf8_limit_counts_encoded_bytes_not_characters(tmp_path):
    target = tmp_path / "SKILL.md"
    text = "界" * 4
    target.write_bytes(text.encode("utf-8"))
    assert SkillCatalog._read(tmp_path, "SKILL.md", 12) == text
    with pytest.raises(ToolError, match="invalid_skill_resource"):
        SkillCatalog._read(tmp_path, "SKILL.md", 11)


@pytest.mark.features("E1")
@pytest.mark.scenario("SKILL-RESOURCE-INVALID-UTF8")
def test_invalid_text_is_a_bounded_typed_error(tmp_path):
    (tmp_path / "SKILL.md").write_bytes(b"synthetic private resource\xff")
    with pytest.raises(ToolError, match="^invalid_skill_resource$"):
        SkillCatalog._read(tmp_path, "SKILL.md", 128)
