"""Provider selection controls and failure policy; synthetic validation responses."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.providers.live import ProviderError
from reachy_brain.web.app import create_app


@pytest.mark.features("C5", "C6", "D4")
@pytest.mark.scenario("VOICE-SELECTION-CONTROLS")
def test_selection_persists_without_modifying_exact_voice(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, elevenlabs_voice_id="synthetic-exact-voice"
    )
    with TestClient(create_app(settings, token="test")) as client:
        assert client.post("/api/voice", json={"provider": "openai"}).status_code == 401
        client.headers["Authorization"] = "Bearer test"
        for provider in ["openai", "elevenlabs", "auto", "openai"]:
            result = client.post("/api/voice", json={"provider": provider})
            assert result.json() == {"selected": provider, "applies": "next_conversation_start"}
        assert settings.elevenlabs_voice_id == "synthetic-exact-voice"
        assert client.post("/api/voice", json={"provider": "other"}).status_code == 409
        assert (
            client.post(
                "/api/voice", json={"provider": "auto", "voice_id": "replacement"}
            ).status_code
            == 400
        )
        assert client.post("/api/voice", content="x" * 1025).status_code == 413
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    ) as client:
        client.headers["Authorization"] = "Bearer test"
        assert client.get("/api/voice").json()["selected"] == "openai"


@pytest.mark.features("C5", "C6")
@pytest.mark.scenario("VOICE-EXPLICIT-SELECTION-POLICY")
@pytest.mark.parametrize(
    "requested,valid", [("auto", False), ("elevenlabs", False), ("auto", True), ("openai", False)]
)
async def test_exact_provider_validation_and_auto_fallback(requested, valid):
    validate = AsyncMock(return_value={"valid": valid, "reason": "synthetic-unavailable"})
    core = SimpleNamespace(
        settings=SimpleNamespace(tts_provider=requested),
        voices={"elevenlabs": SimpleNamespace(validate=validate)},
        voice_provider="unselected",
        voice_reason="unselected",
    )
    if requested == "elevenlabs" and not valid:
        with pytest.raises(ProviderError):
            await Conversation.select_voice(core)
        assert core.voice_provider == "unselected"
    else:
        await Conversation.select_voice(core)
        assert core.voice_provider == (
            "elevenlabs" if valid and requested != "openai" else "openai"
        )
    assert validate.await_count == (0 if requested == "openai" else 1)
