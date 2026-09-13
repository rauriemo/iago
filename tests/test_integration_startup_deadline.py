"""Cooperative synthetic adapter stalls; no external servers or credentials."""

import asyncio
import json

import pytest

from reachy_brain.integrations.registry import ActionPolicy, ToolError, ToolRegistry
from reachy_brain.integrations.runtime import IntegrationRuntime


@pytest.mark.features("E1", "D5")
@pytest.mark.scenario("E1-STARTUP-DEADLINE")
@pytest.mark.parametrize("phase", ["entry", "discovery"])
async def test_startup_stall_has_host_deadline(tmp_path, monkeypatch, phase):
    config = dict(
        module="synthetic", account="local", enabled=True, transport="python", timeout=0.01
    )
    path = tmp_path / "installation.json"
    path.write_text(json.dumps({"modules": [config]}), encoding="utf-8")
    cleaned = []

    class Adapter:
        def __init__(self, config):
            pass

        async def __aenter__(self):
            if phase == "entry":
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.append("partial-entry")
            return self

        async def register(self, *args):
            await asyncio.Event().wait()

        async def __aexit__(self, *args):
            cleaned.append("exit")

    monkeypatch.setattr("reachy_brain.integrations.runtime.DirectModule", Adapter)
    runtime = IntegrationRuntime(ToolRegistry(), ActionPolicy(), path)
    # The outer deadline is a test watchdog, not a product timeout or passing substitute.
    with pytest.raises(ToolError, match="integration_startup_timeout"):
        async with asyncio.timeout(1):
            await runtime.__aenter__()
    assert cleaned == (["partial-entry"] if phase == "entry" else ["exit"])
    assert not runtime.modules


@pytest.mark.features("E1", "D5")
@pytest.mark.scenario("E1-STARTUP-TIMEOUT-VALIDATION")
@pytest.mark.parametrize("deadline_value", [True, 0, -1, float("inf"), float("nan"), "10", 61])
async def test_invalid_deadline_rejected_before_construction(tmp_path, monkeypatch, deadline_value):
    path = tmp_path / "installation.json"
    path.write_text(
        json.dumps(
            {
                "modules": [
                    dict(
                        module="synthetic",
                        account="local",
                        enabled=True,
                        transport="python",
                        timeout=deadline_value,
                    )
                ]
            }
        ),
        encoding="utf-8",
    )
    constructed = []

    def forbidden(config):
        constructed.append(config)
        raise AssertionError("invalid timeout reached adapter")

    monkeypatch.setattr("reachy_brain.integrations.runtime.DirectModule", forbidden)
    with pytest.raises(ToolError, match="invalid_integration_configuration"):
        await IntegrationRuntime(ToolRegistry(), ActionPolicy(), path).__aenter__()
    assert not constructed
