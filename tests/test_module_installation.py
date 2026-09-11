"""Base installation validation before adapters can be constructed."""

import json

import pytest

from reachy_brain.integrations.registry import ActionPolicy, ToolError, ToolRegistry
from reachy_brain.integrations.runtime import IntegrationRuntime


@pytest.mark.features("E1")
@pytest.mark.scenario("MODULE-INSTALLATION-VALIDATE-BEFORE-START")
@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", "false"),
        ("enabled", 1),
        ("module", None),
        ("account", {}),
        ("capabilities", "synthetic"),
        ("scopes", [True]),
        ("transport", "unknown"),
        ("transport", []),
        ("transport", {}),
    ],
)
async def test_invalid_later_module_prevents_all_adapter_startup(
    tmp_path, monkeypatch, field, value
):
    first = {
        "module": "first",
        "account": "local",
        "enabled": True,
        "transport": "python",
        "factory": "synthetic:unused",
    }
    second = {**first, "module": "second", field: value}
    path = tmp_path / "installation.json"
    path.write_text(json.dumps({"modules": [first, second]}))
    constructed = []

    def forbidden(config):
        constructed.append(config)
        raise AssertionError("invalid base configuration reached adapter construction")

    monkeypatch.setattr("reachy_brain.integrations.runtime.DirectModule", forbidden)
    runtime = IntegrationRuntime(ToolRegistry(), ActionPolicy(), path)
    with pytest.raises(ToolError, match="^invalid_integration_configuration$"):
        async with runtime:
            pass
    assert not constructed and not runtime.modules


@pytest.mark.features("E1")
@pytest.mark.scenario("MODULE-INSTALLATION-DUPLICATE-NAMESPACE")
async def test_duplicate_disabled_namespaces_are_rejected(tmp_path):
    entry = {"module": "synthetic", "account": "local", "enabled": False}
    path = tmp_path / "installation.json"
    path.write_text(json.dumps({"modules": [entry, entry]}))
    with pytest.raises(ToolError, match="invalid_integration_configuration"):
        async with IntegrationRuntime(ToolRegistry(), ActionPolicy(), path):
            pass


@pytest.mark.features("E1", "D5")
@pytest.mark.scenario("MODULE-ENABLE-REVALIDATES-CONFIGURATION")
def test_enabling_incomplete_disabled_module_preserves_configuration(tmp_path):
    path = tmp_path / "installation.json"
    original = json.dumps(
        {"modules": [{"module": "synthetic", "account": "local", "enabled": False}]}
    ).encode()
    path.write_bytes(original)
    runtime = IntegrationRuntime(ToolRegistry(), ActionPolicy(), path)
    with pytest.raises(ToolError, match="invalid_integration_configuration"):
        runtime.save_module_enabled("synthetic", "local", True)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".integration-*"))
