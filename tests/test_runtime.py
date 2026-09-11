"""Real stdio installation with fake records; no conversation-core modifications or accounts."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from reachy_brain.integrations.mcp_adapter import MCPModule
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Rule,
    ToolError,
    ToolExecutor,
    ToolRegistry,
)
from reachy_brain.integrations.runtime import IntegrationRuntime


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-MCP-RESTART-ACCOUNT-IDENTITY")
@pytest.mark.parametrize("replacement", ["same", "other-store", "other-account", "unknown"])
async def test_reconciliation_after_restart_is_bound_to_store_and_account(tmp_path, replacement):
    config = json.loads(
        await asyncio.to_thread(
            Path("examples/integrations.fake-calendar.json").read_text, encoding="utf-8"
        )
    )["modules"][0]
    config["command"] = sys.executable
    config["args"][-1] = str(tmp_path / "original.sqlite")
    if replacement == "unknown":
        config.pop("identity_probe")
    journal_path = tmp_path / "operations.sqlite"
    context = CallContext("test", 1, operation_id="restart-lost-response")
    key = "calendar__synthetic-a__create_event"
    payload = {"title": "Fake restart", "start": "2030-01-01", "lose_response": True}
    journal = OperationStore(journal_path)
    try:
        async with MCPModule(config) as module:
            registry, policy = ToolRegistry(), ActionPolicy()
            await module.register(registry, policy)
            executor = ToolExecutor(registry, policy, journal)
            policy.set(Rule(key, "write", "allow"))
            assert (await executor.execute(key, payload, context))["status"] == "uncertain"
            await executor.close()
    finally:
        journal.close()
    if replacement == "other-store":
        config["args"][-1] = str(tmp_path / "replacement.sqlite")
    elif replacement == "other-account":
        config["account"] = config["bound_arguments"]["account"] = "synthetic-b"
    journal = OperationStore(journal_path)
    try:
        async with MCPModule(config) as module:
            registry, policy = ToolRegistry(), ActionPolicy()
            await module.register(registry, policy)
            executor = ToolExecutor(registry, policy, journal)
            if replacement == "same":
                registry.connections[("calendar", "synthetic-a")].generation = 7
                assert (await executor.reconcile(context.operation_id, context))[
                    "status"
                ] == "succeeded"
                events = await executor.execute("calendar__synthetic-a__list_events", {}, context)
                assert len(events["result"]["events"]) == 1
            else:
                with pytest.raises(ToolError, match="reconciliation_unavailable"):
                    await executor.reconcile(context.operation_id, context)
                assert journal.get(context.operation_id)["status"] == "uncertain"
            await executor.close()
    finally:
        journal.close()


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-INSTALL-WORKFLOW")
async def test_installed_workflow_and_controlled_write(tmp_path):
    config = json.loads(
        await asyncio.to_thread(Path("examples/integrations.fake-calendar.json").read_text)
    )
    config["modules"][0]["command"] = sys.executable
    config["modules"][0]["args"][-1] = str(tmp_path / "fake.sqlite")
    path = tmp_path / "installation.json"
    path.write_text(json.dumps(config))
    registry, policy = ToolRegistry(), ActionPolicy()
    journal = OperationStore(tmp_path / "operations.sqlite")
    executor = ToolExecutor(registry, policy, journal)
    try:
        async with IntegrationRuntime(registry, policy, path) as runtime:
            ctx = CallContext(
                "test", 1, capabilities=frozenset(runtime.capabilities), operation_id="fake-write"
            )
            assert runtime.status()["workflows"][0]["available"]
            instructions = await executor.execute(
                "workflows__local__load", {"id": "fake-calendar-review"}, ctx
            )
            assert instructions["status"] == "ok"
            key = "calendar__synthetic-a__create_event"
            payload = {"title": "Synthetic approval test", "start": "2030-01-01T12:00:00Z"}
            assert (await executor.execute(key, payload, ctx))["status"] == "confirmation_required"
            proposal = await executor.propose(key, payload, ctx)
            executor.confirm(ctx.operation_id, proposal["binding"], input_kind="action_ui")
            assert (await executor.execute(key, payload, ctx))["status"] == "ok"
            events = await executor.execute("calendar__synthetic-a__list_events", {}, ctx)
            assert len(events["result"]["events"]) == 1
            registry.tools[key].enabled = False
            assert not runtime.status()["workflows"][0]["available"]
            assert (await executor.execute(key, payload, ctx))["status"] == "disabled"
            assert (
                await executor.execute(
                    "workflows__local__load", {"id": "fake-calendar-review"}, ctx
                )
            )["status"] == "missing_capability"
    finally:
        journal.close()


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-MCP-UNCERTAIN-STATUS-POLICY")
async def test_actual_mcp_uncertain_write_uses_separate_read_permission(tmp_path):
    config = json.loads(
        await asyncio.to_thread(
            Path("examples/integrations.fake-calendar.json").read_text, encoding="utf-8"
        )
    )
    config["modules"][0]["command"] = sys.executable
    config["modules"][0]["args"][-1] = str(tmp_path / "fake.sqlite")
    path = tmp_path / "installation.json"
    await asyncio.to_thread(path.write_text, json.dumps(config), encoding="utf-8")
    registry, policy = ToolRegistry(), ActionPolicy()
    journal = OperationStore(tmp_path / "operations.sqlite")
    executor = ToolExecutor(registry, policy, journal)
    try:
        async with IntegrationRuntime(registry, policy, path):
            context = CallContext("test", 1, operation_id="lost-response")
            key = "calendar__synthetic-a__create_event"
            lookup = "calendar__synthetic-a__operation_status"
            payload = {
                "title": "Synthetic uncertain write",
                "start": "2030-01-01",
                "lose_response": True,
            }
            proposal = await executor.propose(key, payload, context)
            executor.confirm(context.operation_id, proposal["binding"], input_kind="action_ui")
            assert (await executor.execute(key, payload, context))["status"] == "uncertain"
            registry.tools[key].enabled = False
            policy.set(Rule(lookup, "read", "deny"))
            with pytest.raises(ToolError, match="reconciliation_not_authorized"):
                await executor.reconcile(context.operation_id, context)
            assert journal.get(context.operation_id)["status"] == "uncertain"
            policy.set(Rule(lookup, "read", "allow"))
            registry.tools[lookup].enabled = False
            with pytest.raises(ToolError, match="disabled"):
                await executor.reconcile(context.operation_id, context)
            registry.tools[lookup].enabled = True
            result = await executor.reconcile(context.operation_id, context)
            assert (
                result["status"] == "succeeded" and result["provider_ref"] == context.operation_id
            )
            events = await executor.execute("calendar__synthetic-a__list_events", {}, context)
            assert len(events["result"]["events"]) == 1
            assert not registry.tools[key].enabled
            await executor.close()
    finally:
        journal.close()
