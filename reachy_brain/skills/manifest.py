"""Typed, bounded metadata for explicitly installed workflows."""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Capability = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Resource = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class WorkflowManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Name
    description: str = Field(min_length=1, max_length=8192)
    tools: list[Name] = Field(default_factory=list, max_length=50)
    capabilities: list[Capability] = Field(default_factory=list, max_length=50)
    resources: list[Resource] = Field(default_factory=list, max_length=50)

    @field_validator("description")
    @classmethod
    def description_not_blank(cls, value):
        if not value.strip():
            raise ValueError("blank_description")
        return value

    @field_validator("tools", "capabilities", "resources")
    @classmethod
    def unique_nonblank(cls, values):
        if len(set(values)) != len(values) or any(not value.strip() for value in values):
            raise ValueError("duplicate_or_blank_entries")
        return values

    @field_validator("resources")
    @classmethod
    def relative_resources(cls, values):
        for value in values:
            path = PurePosixPath(value)
            if (
                "\\" in value
                or ":" in value
                or "\x00" in value
                or path.is_absolute()
                or PureWindowsPath(value).drive
                or ".." in path.parts
                or value in {".", ""}
            ):
                raise ValueError("nonportable_resource_path")
        return values
