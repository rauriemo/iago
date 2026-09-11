"""Style configuration and request wiring; synthetic provider, no language-quality claim."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import DEFAULT_PERSONALITY, RUNTIME_INSTRUCTIONS, Settings
from reachy_brain.providers.live import AstraBrain, ProviderGate
from reachy_brain.web.app import create_app


@pytest.mark.features("C7", "D4", "E1")
@pytest.mark.scenario("C7-PERSONALITY-CONTROLS")
def test_edit_restart_and_rejection_preserve_application_policy(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    app = create_app(settings, token="test")
    with TestClient(app) as client:
        assert (
            client.post("/api/personality", json={"instructions": "Portuguese"}).status_code == 401
        )
        client.headers["Authorization"] = "Bearer test"
        assert client.get("/api/personality").json()["instructions"] == DEFAULT_PERSONALITY
        revision = app.state.executor.policy.revision
        value = "Fale português. Responda brevemente e questione minhas suposições com respeito."
        assert client.post("/api/personality", json={"instructions": value}).status_code == 200
        assert settings.personality == value
        assert app.state.executor.policy.revision == revision
        for invalid in ["", " " * 4, "x" * 8001, None]:
            assert (
                client.post("/api/personality", json={"instructions": invalid}).status_code == 409
            )
        assert client.post("/api/personality", content="x" * 65537).status_code == 413
        assert settings.personality == value
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    ) as client:
        client.headers["Authorization"] = "Bearer test"
        assert client.get("/api/personality").json()["instructions"] == value


@pytest.mark.features("C7", "E1")
@pytest.mark.scenario("C7-ASTRA-INSTRUCTION-WIRING")
async def test_style_reaches_astra_without_removing_runtime_instructions(monkeypatch):
    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def messages(self):
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(id="synthetic-response", usage=None),
            )

        def __aiter__(self):
            return self.messages()

    create = AsyncMock(return_value=Stream())
    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
    monkeypatch.setattr("reachy_brain.providers.live.AsyncOpenAI", lambda **kwargs: client)
    settings = Settings(
        _env_file=None,
        openai_api_key="synthetic",
        iago_development_budget="unlimited",
        personality="Answer in Portuguese.",
    )
    brain = AstraBrain(settings, ProviderGate(settings))
    assert [item async for item in brain.stream([], [])] == [{"type": "done", "usage": {}}]
    request = create.call_args.kwargs
    assert request["model"] == "gpt-6-astra"
    assert RUNTIME_INSTRUCTIONS in request["instructions"]
    assert settings.personality in request["instructions"]
    assert request["store"] is False
    await brain.close()
