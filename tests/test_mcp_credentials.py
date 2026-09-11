"""Real local HTTP/MCP round trips with synthetic bearer tokens and calendar data."""

import asyncio
import socket

import httpx2
import pytest
import uvicorn

from reachy_brain.integrations.fake_calendar import make_server
from reachy_brain.integrations.http_auth import AccountBearer
from reachy_brain.integrations.mcp_adapter import MCPModule
from reachy_brain.integrations.registry import ActionPolicy, ToolError, ToolRegistry


@pytest.mark.features("E1")
@pytest.mark.scenario("MCP-HTTP-ACCOUNT-CREDENTIALS")
async def test_authenticated_transport_and_disconnect(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_MCP_TOKEN", "synthetic-private-bearer")
    backend = make_server(tmp_path / "fake.sqlite").streamable_http_app()
    authenticated = []

    async def app(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if headers.get(b"authorization") != b"Bearer synthetic-private-bearer":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
            authenticated.append(scope["method"])
        await backend(scope, receive, send)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    pytest.fail("Local synthetic server failed to start")
                await asyncio.sleep(0.02)
        url = f"http://127.0.0.1:{port}/mcp"
        async with httpx2.AsyncClient() as anonymous:
            assert (await anonymous.get(url)).status_code == 401
        config = {
            "module": "calendar",
            "account": "synthetic-a",
            "transport": "streamable-http",
            "url": url,
            "scopes": ["calendar.read"],
            "credentials": {"token_environment": "TEST_MCP_TOKEN", "scopes": ["calendar.read"]},
            "tools": {"list_events": {"action": "read", "policy": "allow"}},
            "bound_arguments": {"account": "synthetic-a"},
        }
        async with MCPModule(config) as module:
            registry, policy = ToolRegistry(), ActionPolicy()
            await module.register(registry, policy)
            result = await module.client.call_tool("list_events", {"account": "synthetic-a"})
            assert result.structured_content == {"events": [], "test_only": True}
            assert authenticated.count("POST") >= 2
            with pytest.raises(ToolError, match="account_disconnected"):
                await module.credentials.retrieve("synthetic-b", frozenset())
            with pytest.raises(ToolError, match="missing_scope"):
                await module.credentials.retrieve("synthetic-a", frozenset({"calendar.write"}))
            auth = AccountBearer(module.credentials, "synthetic-a", [], url)
            flow = auth.async_auth_flow(httpx2.Request("GET", "https://other.invalid/mcp"))
            with pytest.raises(ToolError, match="credential_origin_mismatch"):
                await anext(flow)
            assert (
                await module.disconnect()
                == "local_credentials_cleared_remote_revocation_not_configured"
            )
            with pytest.raises(ToolError, match="account_disconnected"):
                await module.http.get(url)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
