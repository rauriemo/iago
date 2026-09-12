"""Synthetic credential-provider behavior; no actual account or credential is used."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from reachy_brain.config import Settings
from reachy_brain.integrations.credentials import Credential, LocalCredentials
from reachy_brain.integrations.direct_adapter import DirectModule
from reachy_brain.integrations.mcp_adapter import MCPModule
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


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-QUEUED-CREDENTIAL-DISCONNECT")
async def test_queued_retrieval_cannot_refresh_after_disconnect():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def refresh(account):
        calls.append(account)
        entered.set()
        await release.wait()
        return Credential(SecretStr("synthetic-token"), frozenset({"read"}), float("inf"))

    provider = LocalCredentials({"a": {"scopes": ["read"]}}, refresh=refresh)
    first = asyncio.create_task(provider.retrieve("a", frozenset({"read"})))
    await entered.wait()
    queued = asyncio.create_task(provider.retrieve("a", frozenset({"read"})))
    # The first refresh holds the account lock while the second retrieval queues.
    await asyncio.sleep(0)
    assert not queued.done()
    try:
        assert await provider.disconnect("a")
    finally:
        release.set()
        outcomes = await asyncio.gather(first, queued, return_exceptions=True)
    assert all(isinstance(outcome, ToolError) for outcome in outcomes)
    assert all(str(outcome) == "account_disconnected" for outcome in outcomes)
    assert calls == ["a"], "Queued retrieval must not refresh a disconnected account"
    assert not provider.cache


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-CREDENTIAL-SCOPE-REFRESH-RACE")
@pytest.mark.parametrize("change", ["revoke_scope", "remove_account"])
async def test_refresh_rechecks_current_account_grants(change):
    entered, release = asyncio.Event(), asyncio.Event()

    async def refresh(account):
        if account == "a":
            entered.set()
            await release.wait()
        return Credential(SecretStr("synthetic-token"), frozenset({"read"}), float("inf"))

    provider = LocalCredentials(
        {"a": {"scopes": ["read"]}, "b": {"scopes": ["read"]}}, refresh=refresh
    )
    task = asyncio.create_task(provider.retrieve("a", frozenset({"read"})))
    await entered.wait()
    if change == "revoke_scope":
        provider.config["a"] = {"scopes": []}
        expected = "missing_scope"
    else:
        del provider.config["a"]
        expected = "account_disconnected"
    release.set()
    outcome = (await asyncio.gather(task, return_exceptions=True))[0]
    assert isinstance(outcome, ToolError) and str(outcome) == expected
    assert "a" not in provider.cache
    # Account A's revocation must not revoke account B's independent grant.
    assert isinstance(await provider.retrieve("b", frozenset({"read"})), SecretStr)


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-ADAPTER-REVOCATION-STATUS")
@pytest.mark.parametrize("module_type", [DirectModule, MCPModule])
@pytest.mark.parametrize("result", ["confirmed", "denied", "exception", "unconfigured"])
async def test_adapter_reports_remote_revocation_outcome(module_type, result):
    calls = []

    async def revoke(account):
        calls.append(account)
        if result == "exception":
            raise RuntimeError("synthetic-private-revocation-response")
        return result == "confirmed"

    # Construction only: no subprocess/transport is started in this status test.
    module = module_type(
        {"module": "synthetic", "account": "a", "transport": "stdio", "command": "unused"}
    )
    module.credentials = LocalCredentials(
        {"a": {"scopes": []}}, revoke=None if result == "unconfigured" else revoke
    )
    status = await module.disconnect()
    expected = {
        "confirmed": "local_credentials_cleared_remote_revocation_confirmed",
        "denied": "local_credentials_cleared_remote_revocation_failed",
        "exception": "local_credentials_cleared_remote_revocation_failed",
        "unconfigured": "local_credentials_cleared_remote_revocation_not_configured",
    }
    assert status == expected[result]
    assert calls == ([] if result == "unconfigured" else ["a"])
    with pytest.raises(ToolError, match="account_disconnected"):
        await module.credentials.retrieve("a", frozenset())
