"""Private, installation-bound tool preferences; never loaded from retrieved content."""

import json
import os
import tempfile
from pathlib import Path

from .registry import Rule, ToolError, digest


class ToolPreferences:
    def __init__(self, path: Path, registry, policy, installation: Path | None):
        self.path, self.registry, self.policy = path, registry, policy
        modules = (
            json.loads(installation.read_text(encoding="utf-8")).get("modules", [])
            if installation
            else []
        )
        installation_refs = {
            (item.get("module"), item.get("account")): digest(
                {key: value for key, value in item.items() if key != "enabled"}
            )
            for item in modules
        }
        self.bindings = {
            key: digest(
                {
                    "installation": installation_refs.get((tool.module, tool.account), "builtins"),
                    "tool": key,
                    "version": tool.version,
                    "action": tool.action,
                    "input": tool.input_schema,
                    "output": tool.output_schema,
                    "scopes": sorted(tool.scopes),
                    "capabilities": sorted(tool.capabilities),
                    "constraints": policy.rules[key].constraints if key in policy.rules else {},
                }
            )
            for key, tool in registry.tools.items()
        }
        self.values = {}
        if path.exists():
            if path.stat().st_size > 131072:
                raise ToolError("tool_preferences_limit")
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, dict) or len(values) > 1000:
                raise ToolError("invalid_tool_preferences")
            for key, value in values.items():
                if (
                    not isinstance(value, dict)
                    or set(value) != {"binding", "enabled", "policy"}
                    or type(value["enabled"]) is not bool
                    or value["policy"] not in {"allow", "confirm", "deny"}
                ):
                    raise ToolError("invalid_tool_preferences")
                self.values[key] = value
            self.apply(self.values)

    def replacement(self, key, *, enabled=None, mode=None):
        if key not in self.bindings:
            raise ToolError("unknown_tool")
        if enabled is not None and type(enabled) is not bool:
            raise ToolError("invalid_tool_preferences")
        if mode is not None and mode not in {"allow", "confirm", "deny"}:
            raise ToolError("invalid_tool_preferences")
        tool = self.registry.tools[key]
        rule = self.policy.rules.get(key)
        return {
            **self.values,
            key: {
                "binding": self.bindings[key],
                "enabled": tool.enabled if enabled is None else enabled,
                "policy": (rule.mode if rule else "deny") if mode is None else mode,
            },
        }

    def write(self, values):
        encoded = json.dumps(values).encode("utf-8")
        if len(encoded) > 131072:
            raise ToolError("tool_preferences_limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".tools-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return values

    def apply(self, values):
        for key, value in values.items():
            if value["binding"] != self.bindings.get(key):
                continue
            tool = self.registry.tools[key]
            tool.enabled = value["enabled"]
            prior = self.policy.rules.get(key)
            self.policy.set(
                Rule(key, tool.action, value["policy"], prior.constraints if prior else {})
            )
        self.values = values
