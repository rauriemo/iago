"""Synthetic configuration growth at actual file opening; preserve concurrent edits."""

import asyncio
import json
from pathlib import Path

import pytest

from reachy_brain.integrations.registry import ActionPolicy, ToolError, ToolRegistry
from reachy_brain.integrations.runtime import IntegrationRuntime


@pytest.mark.features("E1", "D5")
@pytest.mark.scenario("INTEGRATION-CONFIG-GROWTH-BOUND")
@pytest.mark.parametrize("phase", ["startup", "save_initial", "save_compare"])
async def test_growing_configuration_is_bounded_and_preserved(tmp_path, monkeypatch, phase):
    path = tmp_path / "installation.json"
    original = json.dumps(
        {
            "modules": [
                {
                    "module": "synthetic",
                    "account": "local",
                    "enabled": False,
                    "transport": "stdio",
                    "command": "unused-synthetic-command",
                }
            ]
        }
    ).encode()
    grown = original + b" " * 70000
    path.write_bytes(original)
    runtime = IntegrationRuntime(ToolRegistry(), ActionPolicy(), path)
    opened = Path.open
    reads = 0
    trigger = 2 if phase == "save_compare" else 1

    def growing(target, mode="r", *args, **kwargs):
        nonlocal reads
        if target == path and mode in {"r", "rb"}:
            reads += 1
            if reads == trigger:
                with opened(path, "wb") as output:
                    output.write(grown)
        return opened(target, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", growing)
        with pytest.raises(ToolError, match="^integration_configuration_limit$"):
            if phase == "startup":
                async with runtime:
                    pass
            else:
                await asyncio.to_thread(runtime.save_module_enabled, "synthetic", "local", True)
    assert path.read_bytes() == grown
    assert not list(tmp_path.glob(".integration-*"))


@pytest.mark.features("E1", "D5")
@pytest.mark.scenario("INTEGRATION-CONFIG-EXACT-BYTE-LIMIT")
@pytest.mark.parametrize("size", [65536, 65537])
async def test_configuration_exact_limit_is_accepted_without_raising_it(tmp_path, size):
    path = tmp_path / "installation.json"
    content = b"{}" + b" " * (size - 2)
    path.write_bytes(content)
    runtime = IntegrationRuntime(ToolRegistry(), ActionPolicy(), path)
    if size == 65536:
        async with runtime:
            assert not runtime.modules
    else:
        with pytest.raises(ToolError, match="integration_configuration_limit"):
            async with runtime:
                pass
    assert path.read_bytes() == content
