"""Persist provider preference without storing credentials or changing voice IDs."""

import json
import os
import tempfile

from reachy_brain.integrations.registry import ToolError


class VoiceSelection:
    def __init__(self, path):
        self.path = path

    @staticmethod
    def validate(provider):
        if provider not in ("auto", "openai", "elevenlabs"):
            raise ToolError("invalid_voice_provider")
        return provider

    def load(self, default):
        if not self.path.exists():
            return default
        if self.path.stat().st_size > 1024:
            raise ToolError("voice_configuration_limit")
        return self.validate(json.loads(self.path.read_text(encoding="utf-8"))["provider"])

    def write(self, provider):
        provider = self.validate(provider)
        descriptor, temporary = tempfile.mkstemp(prefix=".voice-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"provider": provider}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return provider
