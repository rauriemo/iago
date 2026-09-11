"""Synthetic installed manifests; reject malformed metadata without publishing partial tools."""

import json

import pytest

from reachy_brain.integrations.registry import ToolError
from reachy_brain.skills.catalog import SkillCatalog


def install(root, name, metadata):
    path = root / name
    path.mkdir()
    (path / "workflow.json").write_text(json.dumps(metadata), encoding="utf-8")
    (path / "SKILL.md").write_text("Synthetic instructions.")
    return {"path": str(path), "trusted": True, "enabled": True}


@pytest.mark.features("E1")
@pytest.mark.scenario("SKILL-MANIFEST-STRICT-VALIDATION")
@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 12),
        ("description", []),
        ("description", " "),
        ("tools", "read_tool"),
        ("capabilities", [False]),
        ("resources", ["../outside.md"]),
        ("resources", ["C:/outside.md"]),
        ("resources", ["file:stream"]),
        ("resources", ["a\\b.md"]),
        ("tools", ["read", "read"]),
        ("permissions", ["allow_all"]),
    ],
)
def test_invalid_manifest_is_typed_and_does_not_leave_partial_catalog(tmp_path, field, value):
    good = {"id": "good", "description": "Synthetic valid workflow"}
    bad = {"id": "bad", "description": "synthetic-private-description", field: value}
    catalog = SkillCatalog([install(tmp_path, "good", good), install(tmp_path, "bad", bad)])
    with pytest.raises(ToolError, match="^invalid_skill_manifest$"):
        catalog.discover(set(), set())
    assert catalog.entries == {}
    with pytest.raises(ToolError, match="skill_unavailable"):
        catalog.load("good")


@pytest.mark.features("E1")
@pytest.mark.scenario("SKILL-INSTALLATION-BOOLEAN-TRUST")
@pytest.mark.parametrize(
    "key,value", [("trusted", "false"), ("trusted", 1), ("enabled", "false"), ("enabled", 1)]
)
def test_truthy_nonboolean_flags_cannot_grant_installation_trust(tmp_path, key, value):
    entry = install(tmp_path, "workflow", {"id": "synthetic", "description": "Synthetic"})
    entry[key] = value
    catalog = SkillCatalog([entry])
    with pytest.raises(ToolError, match="invalid_skill_installation"):
        catalog.discover(set(), set(), include_disabled=True)
    assert not catalog.entries


@pytest.mark.features("E1")
@pytest.mark.scenario("SKILL-MANIFEST-DECLARED-RESOURCE")
def test_valid_metadata_preserves_description_bounds_and_declared_loading(tmp_path):
    entry = install(
        tmp_path,
        "workflow",
        {"id": "synthetic", "description": "x" * 1100, "resources": ["guide.md"]},
    )
    (tmp_path / "workflow" / "guide.md").write_text("Synthetic guide.")
    catalog = SkillCatalog([entry])
    assert len(catalog.discover(set(), set())[0]["description"]) == 1000
    assert catalog.load("synthetic", "guide.md") == "Synthetic guide."
    with pytest.raises(ToolError, match="resource_not_declared"):
        catalog.load("synthetic", "undeclared.md")


@pytest.mark.features("E1")
@pytest.mark.scenario("SKILL-INSTALLATION-PATH-VALIDATION")
@pytest.mark.parametrize("path", [None, 12, " "])
def test_trusted_installation_requires_a_nonempty_string_path(path):
    catalog = SkillCatalog([{"path": path, "trusted": True, "enabled": True}])
    with pytest.raises(ToolError, match="invalid_skill_installation"):
        catalog.discover(set(), set())
