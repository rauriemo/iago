"""Installed Python adapter behavior with a deliberately synthetic local counter."""

import asyncio
import json
from pathlib import Path

import pytest

from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.integrations.runtime import IntegrationRuntime


async def installation(tmp_path, change=None):
    config = json.loads(
        await asyncio.to_thread(
            Path("examples/integrations.fake-direct.json").read_text, encoding="utf-8"
        )
    )
    if change:
        change(config["modules"][0])
    path = tmp_path / "installation.json"
    await asyncio.to_thread(path.write_text, json.dumps(config), encoding="utf-8")
    return path


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-DIRECT-INSTALLATION")
async def test_direct_discovery_confirmed_write_and_lifecycle(tmp_path):
    path = await installation(tmp_path)
    registry, policy = ToolRegistry(), ActionPolicy()
    journal = OperationStore(tmp_path / "operations.sqlite")
    executor = ToolExecutor(registry, policy, journal)
    try:
        async with IntegrationRuntime(registry, policy, path) as runtime:
            context = CallContext("test", 1, operation_id="counter-change")
            keys = {tool["name"] for tool in registry.discover(policy, context)}
            assert {
                "counter__synthetic-a__read_counter",
                "counter__synthetic-a__set_counter",
            } <= keys
            read, write = "counter__synthetic-a__read_counter", "counter__synthetic-a__set_counter"
            assert (await executor.execute(read, {}, context))["result"]["value"] == 0
            assert (await executor.execute(write, {"value": 101}, context))[
                "status"
            ] == "invalid_input"
            assert (await executor.execute(write, {"value": 4}, context))[
                "status"
            ] == "confirmation_required"
            proposal = await executor.propose(write, {"value": 4}, context)
            executor.confirm(context.operation_id, proposal["binding"], input_kind="action_ui")
            assert (await executor.execute(write, {"value": 4}, context))["status"] == "ok"
            assert (await executor.execute(read, {}, context))["result"]["value"] == 4
            assert (await executor.execute(write, {"value": 4}, context))["duplicate"]
            registry.tools[read].enabled = False
            assert (await executor.execute(read, {}, context))["status"] == "disabled"
            adapter = runtime.modules[0].adapter
            await executor.close()
        assert adapter.closed
    finally:
        journal.close()


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-DIRECT-INSTALLATION-POLICY")
@pytest.mark.parametrize(
    "case", ["disabled", "untrusted", "downgrade", "capability", "invalid_policy"]
)
async def test_installation_policy_boundaries(tmp_path, case):
    def change(config):
        if case == "disabled":
            config.update(enabled=False, factory="missing.module:create")
        elif case == "untrusted":
            config.update(trusted=False, factory="missing.module:create")
        elif case == "downgrade":
            config["tools"]["set_counter"]["action"] = "read"
        elif case == "invalid_policy":
            config["tools"]["set_counter"]["policy"] = "anything"
        else:
            config["tools"]["read_counter"]["capabilities"] = ["unavailable_runtime"]

    path = await installation(tmp_path, change)
    registry, policy = ToolRegistry(), ActionPolicy()
    runtime = IntegrationRuntime(registry, policy, path)
    if case in {"untrusted", "downgrade", "invalid_policy"}:
        with pytest.raises(
            ToolError,
            match={
                "untrusted": "untrusted_direct_adapter",
                "downgrade": "adapter_action_mismatch",
                "invalid_policy": "invalid_action_policy",
            }[case],
        ):
            async with runtime:
                pytest.fail("Invalid installation entered")
        return
    async with runtime:
        if case == "disabled":
            assert not runtime.modules
            assert runtime.diagnostics[0]["status"] == "disabled"
        else:
            journal = OperationStore(tmp_path / "operations.sqlite")
            executor = ToolExecutor(registry, policy, journal)
            try:
                result = await executor.execute(
                    "counter__synthetic-a__read_counter", {}, CallContext("s", 1)
                )
                assert result["status"] == "missing_capability"
                await executor.close()
            finally:
                journal.close()
