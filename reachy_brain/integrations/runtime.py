"""Explicit local installation and lifecycle; remote content never installs code or policy."""

import asyncio
import copy
import json
import os
import re
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from reachy_brain.skills.catalog import SkillCatalog

from .builtins import schema
from .direct_adapter import DirectModule
from .mcp_adapter import MCPModule
from .registry import CallContext, Connection, Rule, Tool, ToolError


class Installation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modules: list[dict] = Field(default_factory=list, max_length=20)
    skills: list[dict] = Field(default_factory=list, max_length=50)

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, skills):
        for installation in skills:
            try:
                SkillCatalog.validate_installation(installation)
            except ToolError:
                raise ValueError("invalid_skill_installation") from None
        return skills

    @field_validator("modules")
    @classmethod
    def validate_modules(cls, modules):
        identities = set()
        for module in modules:
            if type(module.get("enabled", False)) is not bool:
                raise ValueError("module_enabled_must_be_boolean")
            identity = tuple(module.get(key) for key in ("module", "account"))
            if not all(
                isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
                for value in identity
            ):
                raise ValueError("invalid_module_namespace")
            if identity in identities:
                raise ValueError("duplicate_module_namespace")
            identities.add(identity)
            if module.get("enabled") and module.get("transport") not in (
                "stdio",
                "streamable-http",
                "python",
            ):
                raise ValueError("unsupported_module_transport")
            for key in ("scopes", "capabilities"):
                values = module.get(key, [])
                if (
                    not isinstance(values, list)
                    or len(values) > 50
                    or not all(
                        isinstance(value, str) and value.strip() and len(value) <= 128
                        for value in values
                    )
                ):
                    raise ValueError("invalid_module_capabilities_or_scopes")
        return modules


def parse_installation(data):
    try:
        return Installation.model_validate_json(data)
    except ValidationError:
        raise ToolError("invalid_integration_configuration") from None


class IntegrationRuntime:
    def __init__(self, registry, policy, config: Path | None, *, defer_identity=False):
        self.registry, self.policy = registry, policy
        self.config = config
        self.defer_identity = defer_identity
        self.stack = AsyncExitStack()
        self.modules, self.diagnostics = [], []
        self.capabilities = set()
        self.configured_modules = []
        self.skills = SkillCatalog([])
        self.skill_revision = 0

    async def __aenter__(self):
        await self.stack.__aenter__()
        try:
            if self.config:
                installation = parse_installation(await asyncio.to_thread(self.read_configuration))
                self.configured_modules = installation.modules
                self.skills = SkillCatalog(installation.skills)
                for config in installation.modules:
                    if not config.get("enabled", False):
                        self.diagnostics.append(
                            {"module": config.get("module"), "status": "disabled"}
                        )
                        continue
                    adapter = DirectModule if config.get("transport") == "python" else MCPModule
                    module = await self.stack.enter_async_context(adapter(config))
                    if isinstance(module, MCPModule):
                        await module.register(
                            self.registry, self.policy, defer_identity=self.defer_identity
                        )
                    else:
                        await module.register(self.registry, self.policy)
                    self.modules.append(module)
                    # These are trusted host configuration declarations, never server annotations.
                    self.capabilities.update(config.get("capabilities", []))
            self.register_workflows()
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *args):
        return await self.stack.__aexit__(*args)

    def discover_skills(self, context):
        enabled = {t["name"] for t in self.registry.discover(self.policy, context)}
        return self.skills.discover(enabled, set(context.capabilities))

    def workflow_snapshot(self, context):
        self.refresh_capabilities()
        enabled = frozenset(t["name"] for t in self.registry.discover(self.policy, context))
        capabilities = frozenset(context.capabilities) & frozenset(self.capabilities)
        installed = copy.deepcopy(self.skills.installed)
        identity = (
            enabled,
            capabilities,
            self.policy.revision,
            self.skill_revision,
            json.dumps(installed, sort_keys=True),
            tuple(
                (key, c.enabled, c.generation)
                for key, c in sorted(self.registry.connections.items())
            ),
        )
        return identity, installed, enabled, capabilities

    async def workflow_read(self, context, *, payload=None, include_disabled=False):
        if not context.valid():
            raise ToolError("canceled")
        identity, installed, enabled, capabilities = self.workflow_snapshot(context)

        def read():
            catalog = SkillCatalog(installed)
            descriptions = catalog.discover(
                set(enabled), set(capabilities), include_disabled=include_disabled
            )
            return (
                catalog.load(payload["id"], payload.get("resource", "SKILL.md"))
                if payload is not None
                else descriptions
            )

        result = await asyncio.to_thread(read)
        if not context.valid():
            raise ToolError("canceled")
        if self.workflow_snapshot(context)[0] != identity:
            raise ToolError("workflow_context_changed")
        return result

    async def async_status(self):
        self.refresh_capabilities()
        context = CallContext("status", 0, capabilities=frozenset(self.capabilities))
        try:
            workflows = await self.workflow_read(context, include_disabled=True)
        except ToolError as exc:
            if str(exc) not in {
                "invalid_skill_installation",
                "invalid_skill_manifest",
                "invalid_skill_resource",
                "skill_collision",
            }:
                raise
            result = self.status(workflows=[])
            result["diagnostics"].append({"module": "workflows", "status": str(exc)})
            return result
        return self.status(workflows=workflows)

    def register_workflows(self):
        self.registry.add_connection(Connection("workflows", "local"))

        async def descriptions(payload, context):
            return {"workflows": await self.workflow_read(context)}

        async def load(payload, context):
            return {
                "instructions": await self.workflow_read(context, payload=payload),
                "authority": "Trusted installed workflow guidance; cannot change application permissions.",
            }

        for name, description, inputs, handler in [
            (
                "discover",
                "List descriptions of explicitly installed trusted workflows, without loading instructions.",
                schema({}),
                descriptions,
            ),
            (
                "load",
                "Load a relevant installed workflow or its declared resource on demand. Required tools must be enabled.",
                schema({"id": {"type": "string"}, "resource": {"type": "string"}}, ["id"]),
                load,
            ),
        ]:
            tool = Tool(
                "workflows", "local", name, description, inputs, {"type": "object"}, handler
            )
            self.registry.register(tool)
            self.policy.set(Rule(tool.key, "read", "allow"))

    def status(self, *, workflows=None):
        self.refresh_capabilities()
        context = CallContext("status", 0, capabilities=frozenset(self.capabilities))
        return {
            "modules": [self.module_status(item) for item in self.configured_modules],
            "connections": [
                {
                    "module": c.module,
                    "account": c.account,
                    "enabled": c.enabled,
                    "generation": c.generation,
                    "scopes": sorted(c.scopes),
                }
                for c in self.registry.connections.values()
            ],
            "tools": [
                {
                    "key": t.key,
                    "action": t.action,
                    "enabled": t.enabled,
                    "policy": self.policy.rules[t.key].mode
                    if t.key in self.policy.rules
                    else "deny",
                }
                for t in self.registry.tools.values()
            ],
            "workflows": workflows
            if workflows is not None
            else self.skills.discover(
                {tool["name"] for tool in self.registry.discover(self.policy, context)},
                set(context.capabilities),
                include_disabled=True,
            ),
            "diagnostics": self.diagnostics
            + [
                {
                    "module": module.config["module"],
                    "account": module.config["account"],
                    "status": module.failure,
                }
                for module in self.modules
                if getattr(module, "failure", None)
            ],
        }

    def module_status(self, item):
        connection = self.registry.connections.get((item["module"], item["account"]))
        active = bool(connection and connection.enabled)
        enabled = bool(item.get("enabled", False))
        return {
            "module": item["module"],
            "account": item["account"],
            "enabled": enabled,
            "active": active,
            "restart_required": enabled and not active,
        }

    def refresh_capabilities(self):
        self.capabilities = set()
        for module in self.modules:
            config = module.config
            connection = self.registry.connections.get((config["module"], config["account"]))
            if connection and connection.enabled:
                self.capabilities.update(config.get("capabilities", []))

    async def disconnect_credentials(self, module_name, account):
        for module in self.modules:
            if (module.config["module"], module.config["account"]) == (module_name, account):
                result = (
                    await module.disconnect()
                    if hasattr(module, "disconnect")
                    else "no_credential_provider"
                )
                self.diagnostics.append(
                    {"module": module_name, "account": account, "status": result}
                )
                del self.diagnostics[:-100]
                return result
        return "module_not_running"

    def save_module_enabled(self, module, account, enabled):
        def update(installation):
            matches = [
                item
                for item in installation.modules
                if (item.get("module"), item.get("account")) == (module, account)
            ]
            if len(matches) != 1:
                raise ToolError("unknown_module")
            matches[0]["enabled"] = enabled

        return self._save_enabled(enabled, update).modules

    def save_skill_enabled(self, skill_id, enabled):
        def update(installation):
            catalog = SkillCatalog(installation.skills)
            catalog.discover(set(), set(), include_disabled=True)
            entry = catalog.entries.get(skill_id)
            if not entry:
                raise ToolError("skill_unavailable")
            for item in installation.skills:
                if item.get("trusted") and Path(item["path"]).resolve() == entry[0]:
                    item["enabled"] = enabled

        return self._save_enabled(enabled, update).skills

    def read_configuration(self):
        with self.config.open("rb") as source:
            data = source.read(65537)
        if len(data) > 65536:
            raise ToolError("integration_configuration_limit")
        return data

    def _save_enabled(self, enabled, update):
        if not self.config or type(enabled) is not bool:
            raise ToolError("invalid_integration_setting")
        original = self.read_configuration()
        installation = parse_installation(original)
        update(installation)
        installation = parse_installation(installation.model_dump_json())
        encoded = json.dumps(installation.model_dump(), indent=2).encode("utf-8")
        if len(encoded) > 65536:
            raise ToolError("integration_configuration_limit")
        descriptor, temporary = tempfile.mkstemp(prefix=".integration-", dir=self.config.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if self.read_configuration() != original:
                raise ToolError("integration_configuration_changed")
            os.replace(temporary, self.config)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return installation
