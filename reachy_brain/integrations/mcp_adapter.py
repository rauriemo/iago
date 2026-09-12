"""MCP 2.x client adapter. Local configuration, not server annotations, grants actions."""

import asyncio
import os
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from .credentials import configured_credentials
from .http_auth import AccountBearer
from .registry import Connection, Rule, Tool, ToolError, bounded, digest


class MCPModule:
    def __init__(self, config: dict):
        self.config = config
        self.stack = AsyncExitStack()
        self.http = None
        self.connection = None
        self.failure = None
        self.owner = None
        self.stop_owner = asyncio.Event()
        self.credentials = configured_credentials(config)
        transport = config["transport"]
        if transport == "stdio":
            if self.credentials:
                raise ToolError("stdio_credentials_use_explicit_environment")
            # No shell expansion and no inherited provider credentials.
            safe_env = {
                k: os.environ[k]
                for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE")
                if k in os.environ
            }
            safe_env.update(config.get("environment", {}))
            server = StdioServerParameters(
                command=config["command"],
                args=config.get("args", []),
                cwd=config.get("cwd"),
                env=safe_env,
            )
        elif transport == "streamable-http":
            endpoint = urlsplit(config["url"])
            if endpoint.username or endpoint.password or endpoint.fragment:
                raise ToolError("invalid_endpoint")
            if endpoint.scheme != "https" and not (
                endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
            ):
                raise ToolError("insecure_endpoint")
            server = config["url"]
            if self.credentials:
                self.http = httpx2.AsyncClient(
                    auth=AccountBearer(
                        self.credentials, config["account"], config.get("scopes", []), server
                    ),
                    timeout=httpx2.Timeout(config.get("timeout", 10), read=300),
                    trust_env=False,
                )
                server = streamable_http_client(server, http_client=self.http)
        else:
            raise ToolError("unsupported_transport")
        self.client = Client(server, read_timeout_seconds=config.get("timeout", 10))

    async def __aenter__(self):
        ready = asyncio.get_running_loop().create_future()
        self.owner = asyncio.create_task(self.run_connection(ready))
        try:
            await ready
        except BaseException:
            self.owner.cancel()
            await asyncio.gather(self.owner, return_exceptions=True)
            raise
        return self

    async def __aexit__(self, *args):
        self.stop_owner.set()
        if self.owner:
            await asyncio.shield(self.owner)

    async def run_connection(self, ready):
        # SDK task groups belong to this task, never the application lifespan.
        try:
            async with self.stack:
                if self.http:
                    await self.stack.enter_async_context(self.http)
                await self.stack.enter_async_context(self.client)
                if not ready.done():
                    ready.set_result(None)
                await self.stop_owner.wait()
        except BaseException:
            self.failure = "mcp_connection_lost"
            if not ready.done():
                ready.set_exception(ToolError("mcp_connection_failed"))
        finally:
            if self.connection:
                self.connection.disconnect()
            await self.disconnect()

    def check_connection(self):
        if self.failure or (self.owner and self.owner.done()):
            raise ToolError("mcp_connection_lost")

    async def disconnect(self):
        if self.credentials:
            return await self.credentials.disconnect_status(self.config["account"])
        return "no_credential_provider"

    async def register(self, registry, policy, *, defer_identity=False):
        config = self.config
        self.check_connection()
        connection = Connection(config["module"], config["account"], set(config.get("scopes", [])))
        discovered = await self.client.list_tools()
        if len(discovered.tools) > 50:
            raise ToolError("tool_catalog_limit")
        registry.add_connection(connection)
        self.connection = connection
        for remote in discovered.tools:
            permission = config.get("tools", {}).get(remote.name)
            if not permission:
                continue
            name = remote.name
            operation_argument = permission.get("operation_argument")

            async def call(
                payload, context, remote_name=name, operation_argument=operation_argument
            ):
                self.check_connection()
                arguments = dict(payload)
                # Account binding cannot be overridden by model-provided input.
                arguments.update(config.get("bound_arguments", {}))
                if operation_argument:
                    arguments[operation_argument] = context.operation_id
                result = await self.client.call_tool(remote_name, arguments)
                self.check_connection()
                if result.is_error:
                    raise ToolError("provider_error")
                output = result.structured_content
                if output is None:
                    output = {"content": [c.model_dump(mode="json") for c in result.content]}
                bounded(output, 65536, 50)
                return output

            schema = dict(remote.input_schema)
            schema["properties"] = dict(schema.get("properties", {}))
            hidden = set(config.get("bound_arguments", {})) | {operation_argument or ""}
            for key in hidden:
                schema["properties"].pop(key, None)
            schema["required"] = [k for k in schema.get("required", []) if k not in hidden]
            tool = Tool(
                config["module"],
                config["account"],
                name,
                (remote.description or name)[:1000],
                schema,
                remote.output_schema or {"type": "object"},
                call,
                action=permission["action"],
                timeout=config.get("timeout", 10),
                scopes=frozenset(permission.get("scopes", [])),
                capabilities=frozenset(permission.get("capabilities", [])),
                reconcile_tool=(
                    f"{config['module']}__{config['account']}__{permission['reconcile_tool']}"
                    if permission.get("reconcile_tool")
                    else None
                ),
                cancel_tool=(
                    f"{config['module']}__{config['account']}__{permission['cancel_tool']}"
                    if permission.get("cancel_tool")
                    else None
                ),
            )
            registry.register(tool)
            policy.set(
                Rule(
                    tool.key,
                    tool.action,
                    permission.get("policy", "deny"),
                    permission.get("constraints", {}),
                )
            )

        if not defer_identity:
            await self.identify(registry, policy)

    async def identify(self, registry, policy):
        config = self.config
        connection = registry.connections[(config["module"], config["account"])]
        # Only an explicitly installed adapter contract can select an identity probe.
        # This identifies a trusted server's resource; it is not authentication of an
        # arbitrary server. Unknown adapters keep an ephemeral connection identity.
        probe = config.get("identity_probe")
        if probe:
            permission = config.get("tools", {}).get(probe["tool"], {})
            if (
                permission.get("action") != "read"
                or permission.get("policy") != "allow"
                or permission.get("constraints")
                or permission.get("capabilities")
                or permission.get("scopes")
                or f"{config['module']}__{config['account']}__{probe['tool']}" not in registry.tools
            ):
                raise ToolError("identity_probe_not_authorized")
            tool = registry.tools[f"{config['module']}__{config['account']}__{probe['tool']}"]
            if not tool.enabled or not connection.enabled or policy.decision(tool, {}) != "allow":
                return  # No identity proof; old uncertain writes remain unreconciled.
            result = await self.client.call_tool(probe["tool"], config.get("bound_arguments", {}))
            identity = result.structured_content
            bounded(identity, 2048, 10)
            if (
                result.is_error
                or not isinstance(identity, dict)
                or set(identity) != {"issuer", "resource", "account"}
                or any(
                    not isinstance(value, str) or not value or len(value) > 256
                    for value in identity.values()
                )
                or identity["issuer"] != probe["issuer"]
                or identity["account"] != config["account"]
            ):
                raise ToolError("invalid_connection_identity")
            connection.identity = digest(identity)
