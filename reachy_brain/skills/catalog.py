from pathlib import Path

from pydantic import ValidationError

from reachy_brain.integrations.registry import ToolError
from reachy_brain.skills.manifest import WorkflowManifest


class SkillCatalog:
    """A small JSON manifest permits metadata-first discovery without reading SKILL.md."""

    def __init__(self, installed: list[dict]):
        self.installed = installed
        self.entries = {}

    def discover(self, enabled_tools: set[str], capabilities: set[str], *, include_disabled=False):
        self.entries = {}
        entries = {}
        result = []
        for installation in self.installed[:50]:
            if any(
                type(installation.get(key, False)) is not bool for key in ("trusted", "enabled")
            ):
                raise ToolError("invalid_skill_installation")
            if not installation.get("trusted") or (
                not installation.get("enabled") and not include_disabled
            ):
                continue
            location = installation.get("path")
            if not isinstance(location, str) or not location.strip():
                raise ToolError("invalid_skill_installation")
            root = Path(location).resolve(strict=True)
            manifest = self._read(root, "workflow.json", 8192)
            try:
                metadata = WorkflowManifest.model_validate_json(manifest).model_dump()
            except ValidationError:
                raise ToolError("invalid_skill_manifest") from None
            if metadata["id"] in entries:
                raise ToolError("skill_collision")
            missing = sorted(set(metadata.get("tools", [])) - enabled_tools)
            missing += sorted(set(metadata.get("capabilities", [])) - capabilities)
            if not installation.get("enabled"):
                missing.append("disabled")
            entries[metadata["id"]] = (root, metadata, missing)
            result.append(
                {
                    "id": metadata["id"],
                    "description": metadata["description"][:1000],
                    "enabled": bool(installation.get("enabled")),
                    "available": not missing,
                    "missing": missing,
                }
            )
        self.entries = entries
        return result

    @staticmethod
    def _read(root, relative, maximum):
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ToolError("invalid_skill_resource")
        with path.open("rb") as source:
            data = source.read(maximum + 1)
        if len(data) > maximum:
            raise ToolError("invalid_skill_resource")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError("invalid_skill_resource") from None

    def load(self, skill_id, resource="SKILL.md"):
        if skill_id not in self.entries:
            raise ToolError("skill_unavailable")
        root, metadata, missing = self.entries[skill_id]
        if missing:
            raise ToolError("missing_capability")
        if resource != "SKILL.md" and resource not in metadata.get("resources", []):
            raise ToolError("resource_not_declared")
        return self._read(root, resource, 32768)
