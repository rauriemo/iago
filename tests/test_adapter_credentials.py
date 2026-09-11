"""Synthetic credential-provider behavior; no actual account or credential is used."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from reachy_brain.config import Settings
from reachy_brain.integrations.credentials import Credential, LocalCredentials
from reachy_brain.integrations.registry import CallContext, ToolError
from reachy_brain.web.app import create_app


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-DIRECT-CREDENTIAL-DISCONNECT")
def test_private_credentials_and_persistent_disconnect(tmp_path, monkeypatch):
    secret = "synthetic-private-counter-credential"
    monkeypatch.setenv("TEST_COUNTER_TOKEN", secret)
    config = json.loads(Path("examples/integrations.fake-direct.json").read_text(encoding="utf-8"))
    config["modules"][0]["credentials"] = {
        "token_environment": "TEST_COUNTER_TOKEN",
        "scopes": ["counter.read"],
    }
    path = tmp_path / "installation.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", integration_config=path)
    with TestClient(create_app(settings, token="test")) as client:
        client.headers["Authorization"] = "Bearer test"
        executor = client.app.state.executor
        context = CallContext("s", 1)
        key = "counter__synthetic-a__read_counter"
        assert client.portal.call(executor.execute, key, {}, context)["status"] == "ok"
        result = client.get("/api/integrations")
        assert secret not in result.text
        result = client.post(
            "/api/integrations",
            json={"action": "disconnect", "module": "counter", "account": "synthetic-a"},
        )
        assert result.status_code == 200 and secret not in result.text
        assert (
            result.json()["diagnostics"][-1]["status"]
            == "local_credentials_cleared_remote_revocation_not_configured"
        )
        assert client.portal.call(executor.execute, key, {}, context)["status"] == "disabled"
        assert secret not in json.dumps(executor.diagnostics)
    with TestClient(create_app(settings, token="test")) as client:
        assert key not in client.app.state.executor.registry.tools
    assert secret not in path.read_text(encoding="utf-8")


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-CREDENTIAL-REFRESH-DISCONNECT-RACE")
async def test_refresh_cannot_restore_disconnected_account_or_expand_scopes():
    entered, release = asyncio.Event(), asyncio.Event()

    async def refresh(account):
        entered.set()
        await release.wait()
        return Credential(SecretStr("synthetic-token"), frozenset({"read"}), float("inf"))

    async def revoke(account):
        raise OSError("synthetic-revocation-failure")

    provider = LocalCredentials({"a": {"scopes": ["read"]}}, refresh=refresh, revoke=revoke)
    with pytest.raises(ToolError, match="account_disconnected"):
        await provider.retrieve("b", frozenset({"read"}))
    with pytest.raises(ToolError, match="missing_scope"):
        await provider.retrieve("a", frozenset({"write"}))
    task = asyncio.create_task(provider.retrieve("a", frozenset({"read"})))
    await entered.wait()
    assert not await provider.disconnect("a")
    release.set()
    with pytest.raises(ToolError, match="account_disconnected"):
        await task
    assert not provider.cache

    async def expired(account):
        return Credential(SecretStr("synthetic-expired"), frozenset({"read"}), 0)

    expired_provider = LocalCredentials({"a": {"scopes": ["read"]}}, refresh=expired)
    with pytest.raises(ToolError, match="credential_expired"):
        await expired_provider.retrieve("a", frozenset({"read"}))
    assert not expired_provider.cache
