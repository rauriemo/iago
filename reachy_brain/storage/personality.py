"""Bounded local style preferences, independent of tool authorization."""

import json
import os
import tempfile

from reachy_brain.integrations.registry import ToolError


class PersonalityStore:
    def __init__(self, path):
        self.path = path

    @staticmethod
    def validate(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 8000:
            raise ToolError("invalid_personality")
        return value

    def load(self, default):
        if not self.path.exists():
            return default
        if self.path.stat().st_size > 65536:
            raise ToolError("personality_storage_limit")
        return self.validate(json.loads(self.path.read_text(encoding="utf-8"))["instructions"])

    def write(self, value):
        value = self.validate(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".personality-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"instructions": value}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return value
