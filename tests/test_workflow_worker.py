"""Real skill files with held reads; synthetic capabilities, no external connectors."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Connection,
    Rule,
    ToolError,
    ToolRegistry,
)
from reachy_brain.integrations.runtime import IntegrationRuntime
from reachy_brain.skills.catalog import SkillCatalog


@pytest.mark.features("E1", "C2")
@pytest.mark.scenario("WORKFLOW-READ-WORKER-STATE-FENCE")
@pytest.mark.parametrize("operation", ["discover", "load", "status"])
@pytest.mark.parametrize("change", ["none", "policy", "installation_aba", "capability", "cancel"])
async def test_workflow_reads_leave_loop_free_and_reject_changed_context(
    tmp_path, monkeypatch, operation, change
):
    (tmp_path / "workflow.json").write_text(
        json.dumps(
            {
                "id": "synthetic",
                "description": "Synthetic workflow",
                "capabilities": ["synthetic_runtime"],
            }
        )
    )
    (tmp_path / "SKILL.md").write_text("Synthetic instructions; no permissions granted.")
    registry, policy = ToolRegistry(), ActionPolicy()
    runtime = IntegrationRuntime(registry, policy, None)
    runtime.skills = SkillCatalog([{"path": str(tmp_path), "trusted": True, "enabled": True}])
    runtime.skills.entries["sentinel"] = "shared catalog must not be mutated"
    registry.add_connection(Connection("synthetic", "local"))
    runtime.modules = [
        SimpleNamespace(
            config={
                "module": "synthetic",
                "account": "local",
                "capabilities": ["synthetic_runtime"],
            }
        )
    ]
    runtime.register_workflows()
    context = CallContext("synthetic", 0, capabilities=frozenset({"synthetic_runtime"}))
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    read = SkillCatalog._read
    held_resource = "SKILL.md" if operation == "load" else "workflow.json"

    def held(root, relative, maximum):
        if relative == held_resource:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "workflow read blocked the event loop"
        return read(root, relative, maximum)

    monkeypatch.setattr(SkillCatalog, "_read", staticmethod(held))
    request = asyncio.create_task(
        runtime.async_status()
        if operation == "status"
        else runtime.workflow_read(
            context, payload={"id": "synthetic"} if operation == "load" else None
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert not request.done()
        assert runtime.skills.entries == {"sentinel": "shared catalog must not be mutated"}
        if change == "policy":
            policy.set(Rule("workflows__local__load", "read", "deny"))
            policy.set(Rule("workflows__local__load", "read", "allow"))
        elif change == "installation_aba":
            runtime.skill_revision += 2
        elif change == "capability":
            registry.connections[("synthetic", "local")].disconnect()
        elif change == "cancel":
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, 1)
        release.set()
        if change == "none":
            result = await request
            assert result
        elif change != "cancel":
            with pytest.raises(ToolError, match="workflow_context_changed"):
                await request
        assert runtime.skills.entries == {"sentinel": "shared catalog must not be mutated"}
    finally:
        release.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
