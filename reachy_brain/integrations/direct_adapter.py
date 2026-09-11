"""Explicit trusted Python installation; policy stays in the host registry."""

import asyncio
import importlib
import re
from dataclasses import replace

from .credentials import configured_credentials
from .registry import Connection, Rule, Tool, ToolError


class DirectModule:
    def __init__(self, config):
        self.config = config
        self.adapter = None
        self.credentials = configured_credentials(config)

    async def __aenter__(self):
        config = self.config
        if config.get("trusted") is not True:
            raise ToolError("untrusted_direct_adapter")
        factory = config.get("factory", "")
        if not isinstance(factory, str) or not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", factory
        ):
            raise ToolError("invalid_adapter_factory")
        module, name = factory.split(":")
        installed = await asyncio.to_thread(importlib.import_module, module)
        creator = getattr(installed, name)
        arguments = {"credential_provider": self.credentials} if self.credentials else {}
        self.adapter = creator(
            config.get("options", {}), config["module"], config["account"], **arguments
        )
        await self.adapter.__aenter__()
        return self

    async def __aexit__(self, *args):
        try:
            return await self.adapter.__aexit__(*args)
        finally:
            if self.credentials:
                await self.credentials.disconnect(self.config["account"])

    async def disconnect(self):
        if self.credentials:
            await self.credentials.disconnect(self.config["account"])
            return "local_credentials_cleared_remote_revocation_not_configured"
        return "no_credential_provider"

    async def register(self, registry, policy):
        config = self.config
        catalog = await self.adapter.list_tools()
        if not isinstance(catalog, list) or len(catalog) > 50:
            raise ToolError("tool_catalog_limit")
        connection = Connection(config["module"], config["account"], set(config.get("scopes", [])))
        registry.add_connection(connection)
        for tool in catalog:
            if not isinstance(tool, Tool) or (tool.module, tool.account) != (
                connection.module,
                connection.account,
            ):
                raise ToolError("invalid_adapter_namespace")
            permission = config.get("tools", {}).get(tool.name)
            if not permission:
                continue
            if permission.get("action") != tool.action:
                raise ToolError("adapter_action_mismatch")
            # Configuration can impose additional requirements, not remove adapter requirements.
            registered = replace(
                tool,
                enabled=tool.enabled and permission.get("enabled", True),
                scopes=tool.scopes | frozenset(permission.get("scopes", [])),
                capabilities=tool.capabilities | frozenset(permission.get("capabilities", [])),
                timeout=min(tool.timeout, config.get("timeout", 10)),
                max_bytes=min(tool.max_bytes, 65536),
                max_items=min(tool.max_items, 50),
            )
            registry.register(registered)
            policy.set(
                Rule(
                    registered.key,
                    registered.action,
                    permission.get("policy", "deny"),
                    permission.get("constraints", {}),
                )
            )
