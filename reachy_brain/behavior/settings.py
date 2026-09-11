"""Host-owned behavior configuration, validated before atomic local persistence."""

import os
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .engine import Rule


class RuleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=100)
    event: str = Field(min_length=1, max_length=100)
    enabled: bool = False
    source: str = Field(default="", max_length=200)
    label: str = Field(default="", max_length=100)
    confidence: float = Field(default=0.8, ge=0, le=1)
    persistence: float = Field(default=0, ge=0, le=3600)
    cooldown: float = Field(default=120, ge=0, le=86400)
    track_cooldown: float = Field(default=120, ge=0, le=86400)
    expiry: float = Field(default=5, gt=0, le=60)
    modes: list[Literal["aware", "conversation"]] = Field(
        default_factory=lambda: ["aware", "conversation"], min_length=1, max_length=2
    )
    action: Literal["greet", "log", "bookmark"] = "greet"
    prompt: str = Field(
        default="Offer a brief greeting grounded in the current event.", max_length=2000
    )
    allow_startup: bool = False


class BehaviorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spontaneous: bool = True
    quiet_start: str = Field(default="", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$|^$")
    quiet_end: str = Field(default="", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$|^$")
    interruption_backoff: float = Field(default=60, ge=0, le=3600)
    conversation_window: float = Field(default=30, ge=5, le=300)
    rules: list[RuleSettings] = Field(max_length=32)

    @model_validator(mode="after")
    def consistent(self):
        if bool(self.quiet_start) != bool(self.quiet_end):
            raise ValueError("Both quiet-hour endpoints are required")
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("Rule IDs must be unique")
        return self

    def quiet(self, now):
        if not self.quiet_start:
            return False
        current = datetime.fromtimestamp(now).strftime("%H:%M")
        if self.quiet_start == self.quiet_end:
            return True
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= current < self.quiet_end
        return current >= self.quiet_start or current < self.quiet_end


class BehaviorConfiguration:
    def __init__(self, path: Path, engine):
        self.path, self.engine = path, engine
        defaults = [
            RuleSettings.model_validate({**asdict(r), "modes": list(r.modes)})
            for r in engine.rules.values()
        ]
        self.value = BehaviorSettings(rules=defaults)
        if path.exists():
            if path.stat().st_size > 131072:
                raise ValueError("behavior_configuration_limit")
            self.value = BehaviorSettings.model_validate_json(path.read_bytes())
        self.apply()

    def apply(self):
        self.engine.rules = {
            r.id: Rule(**{**r.model_dump(), "modes": tuple(r.modes)}) for r in self.value.rules
        }
        self.engine.spontaneous = self.value.spontaneous
        self.engine.pending = None

    def save(self, value):
        replacement = BehaviorSettings.model_validate(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".behavior-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(replacement.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.value = replacement
        self.apply()

    def tick(self, now):
        self.engine.quiet = self.value.quiet(now)
