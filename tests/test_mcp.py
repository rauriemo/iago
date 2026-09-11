"""Actual MCP transport round trips; provider/calendar records are explicitly fake."""

import asyncio
import socket
import subprocess
import sys

import httpx
import pytest
from mcp import Client, StdioServerParameters


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-TRANSPORT")
@pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
async def test_actual_calendar_transports(tmp_path, transport):
    command = [
        "-m",
        "reachy_brain.integrations.fake_calendar",
        "--data",
        str(tmp_path / "fake.sqlite"),
    ]
    process = None
    if transport == "stdio":
        target = StdioServerParameters(command=sys.executable, args=command)
    else:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *command,
            "--transport",
            transport,
            "--port",
            str(port),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        target = f"http://127.0.0.1:{port}/mcp"
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                if process.returncode is not None:
                    pytest.fail("Fake calendar HTTP process exited")
                try:
                    await client.get(target)
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
            else:
                pytest.fail("Fake calendar HTTP server failed to start")
    try:
        async with Client(target) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools.tools} == {
                "list_events",
                "draft_event",
                "create_event",
                "operation_status",
                "connection_identity",
                "request_cancel",
            }
            first = await client.call_tool("list_events", {"account": "synthetic-a"})
            assert first.structured_content["events"] == []
            payload = {
                "account": "synthetic-a",
                "title": "Fake review",
                "start": "2030-01-01T12:00:00Z",
            }
            draft = await client.call_tool("draft_event", payload)
            assert draft.structured_content["title"] == "Fake review"
            assert (
                await client.call_tool("list_events", {"account": "synthetic-a"})
            ).structured_content["events"] == []
            created = await client.call_tool("create_event", {**payload, "operation_id": "op1"})
            assert created.structured_content["provider_ref"] == "op1"
            await client.call_tool("create_event", {**payload, "operation_id": "op1"})
            events = (
                await client.call_tool("list_events", {"account": "synthetic-a"})
            ).structured_content["events"]
            assert len(events) == 1
            assert (
                await client.call_tool("list_events", {"account": "synthetic-b"})
            ).structured_content["events"] == []
            bad = await client.call_tool(
                "create_event", {**payload, "account": "real-account", "operation_id": "bad"}
            )
            assert bad.is_error
            status = await client.call_tool(
                "operation_status",
                {"account": "synthetic-a", "operation_id": "op1"},
            )
            assert status.structured_content["status"] == "succeeded"
            pending = asyncio.create_task(
                client.call_tool(
                    "create_event",
                    {
                        **payload,
                        "operation_id": "pending",
                        "prepare_delay_seconds": 1,
                    },
                )
            )
            try:
                async with asyncio.timeout(3):
                    while True:
                        state = await client.call_tool(
                            "operation_status",
                            {"account": "synthetic-a", "operation_id": "pending"},
                        )
                        if state.structured_content["status"] == "uncertain":
                            break
                        await asyncio.sleep(0.01)
                other = await client.call_tool(
                    "request_cancel",
                    {
                        "account": "synthetic-b",
                        "operation_id": "pending",
                        "request_id": "wrong-account",
                    },
                )
                assert other.structured_content["outcome"] == "unavailable"
                request = {
                    "account": "synthetic-a",
                    "operation_id": "pending",
                    "request_id": "cancel-pending",
                }
                receipt = await client.call_tool("request_cancel", request)
                assert receipt.structured_content["outcome"] == "canceled"
                assert (
                    await client.call_tool("request_cancel", request)
                ).structured_content == receipt.structured_content
                assert (await pending).is_error
                state = await client.call_tool(
                    "operation_status", {"account": "synthetic-a", "operation_id": "pending"}
                )
                assert state.structured_content["status"] == "failed"
                late = await client.call_tool(
                    "request_cancel",
                    {"account": "synthetic-a", "operation_id": "op1", "request_id": "cancel-late"},
                )
                assert late.structured_content["outcome"] == "too_late"
                assert (
                    len(
                        (
                            await client.call_tool("list_events", {"account": "synthetic-a"})
                        ).structured_content["events"]
                    )
                    == 1
                )
            finally:
                await asyncio.gather(pending, return_exceptions=True)
    finally:
        if process:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=10)
