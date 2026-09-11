"""Kill/restart a real loopback MCP process around a synthetic pending calendar write."""

import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from reachy_brain.integrations.mcp_adapter import MCPModule
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import (
    ActionPolicy,
    CallContext,
    Connection,
    Rule,
    Tool,
    ToolExecutor,
    ToolRegistry,
)


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-CANCELLATION-PROVIDER-CRASH-RESTART")
async def test_pending_provider_crash_reconcile_and_cancel_without_replay(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    processes = []

    async def start():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "reachy_brain.integrations.fake_calendar",
            "--data",
            str(tmp_path / "provider.sqlite"),
            "--transport",
            "streamable-http",
            "--port",
            str(port),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        processes.append(process)
        async with httpx.AsyncClient() as client, asyncio.timeout(10):
            while True:
                if process.returncode is not None:
                    pytest.fail("Synthetic MCP process exited during startup")
                try:
                    await client.get(url)
                    return process
                except httpx.ConnectError:
                    await asyncio.sleep(0.02)

    config = json.loads(
        await asyncio.to_thread(
            Path("examples/integrations.fake-calendar.json").read_text, encoding="utf-8"
        )
    )["modules"][0]
    config.update(transport="streamable-http", url=url, timeout=3)
    key = "calendar__synthetic-a__create_event"
    target = CallContext("test", 1, operation_id="interrupted-create")
    payload = {
        "title": "Synthetic pending event",
        "start": "2030-01-01",
        "prepare_delay_seconds": 20,
    }
    try:
        process = await start()
        journal = OperationStore(tmp_path / "journal.sqlite")
        try:
            async with MCPModule(config) as module:
                registry, policy = ToolRegistry(), ActionPolicy()
                await module.register(registry, policy)
                policy.set(Rule(key, "write", "allow"))
                executor = ToolExecutor(registry, policy, journal)
                pending = asyncio.create_task(executor.execute(key, payload, target))
                try:
                    async with asyncio.timeout(5):
                        while True:
                            status = await module.client.call_tool(
                                "operation_status",
                                {"account": "synthetic-a", "operation_id": target.operation_id},
                            )
                            if status.structured_content["status"] == "uncertain":
                                break
                            await asyncio.sleep(0.01)
                    process.kill()
                    await process.wait()
                    assert (await pending)["status"] == "uncertain"
                    assert journal.get(target.operation_id)["status"] == "uncertain"
                    assert module.failure == "mcp_connection_lost"
                    assert not registry.connections[("calendar", "synthetic-a")].enabled

                    async def local_read(payload, context):
                        return {"alive": True}

                    registry.add_connection(Connection("local", "test"))
                    independent = Tool(
                        "local",
                        "test",
                        "read",
                        "Synthetic local read",
                        {"type": "object"},
                        {"type": "object"},
                        local_read,
                    )
                    registry.register(independent)
                    policy.set(Rule(independent.key, "read", "allow"))
                    assert (await executor.execute(independent.key, {}, CallContext("s", 2)))[
                        "result"
                    ] == {"alive": True}
                finally:
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    await executor.close()
        finally:
            journal.close()
        await start()
        journal = OperationStore(tmp_path / "journal.sqlite")
        try:
            async with MCPModule(config) as module:
                registry, policy = ToolRegistry(), ActionPolicy()
                await module.register(registry, policy)
                policy.set(Rule(key, "write", "allow"))
                policy.set(Rule("calendar__synthetic-a__request_cancel", "write", "allow"))
                executor = ToolExecutor(registry, policy, journal)
                try:
                    assert (await executor.reconcile(target.operation_id, target))[
                        "status"
                    ] == "uncertain"
                    repeated = await executor.execute(key, payload, target)
                    assert repeated["duplicate"] and repeated["status"] == "uncertain"
                    cancel = CallContext("controls", 0, operation_id="cancel-after-restart")
                    receipt = await executor.request_cancellation(target.operation_id, cancel)
                    assert receipt["result"]["outcome"] == "canceled"
                    assert journal.get(target.operation_id)["status"] == "uncertain"
                    assert (await executor.reconcile(target.operation_id, target))[
                        "status"
                    ] == "failed"
                    events = await module.client.call_tool(
                        "list_events", {"account": "synthetic-a"}
                    )
                    assert events.structured_content["events"] == []
                finally:
                    await executor.close()
        finally:
            journal.close()
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 10)
