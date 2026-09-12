"""Synthetic direct-adapter startup failures; no external account or provider calls."""

import asyncio
from types import SimpleNamespace

import pytest

from reachy_brain.integrations.direct_adapter import DirectModule
from reachy_brain.integrations.registry import ToolError


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-DIRECT-STARTUP-CREDENTIAL-CLEANUP")
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failed_entry_retires_host_credentials(monkeypatch, failure):
    monkeypatch.setenv("IAGO_SYNTHETIC_STARTUP_TOKEN", "synthetic-private-startup-token")
    module = DirectModule(
        {
            "trusted": True,
            "factory": "synthetic_adapter:create",
            "module": "example",
            "account": "synthetic-a",
            "credentials": {
                "token_environment": "IAGO_SYNTHETIC_STARTUP_TOKEN",
                "scopes": ["read"],
            },
        }
    )

    class Adapter:
        async def __aenter__(self):
            await module.credentials.retrieve("synthetic-a", frozenset({"read"}))
            assert module.credentials.cache
            raise failure("synthetic entry failure")

        async def __aexit__(self, *args):
            pytest.fail("A failed context entry must clean its own adapter resources")

    monkeypatch.setattr(
        "reachy_brain.integrations.direct_adapter.importlib.import_module",
        lambda name: SimpleNamespace(create=lambda *args, **kwargs: Adapter()),
    )
    with pytest.raises(failure, match="synthetic entry failure"):
        await module.__aenter__()
    assert not module.credentials.cache
    with pytest.raises(ToolError, match="account_disconnected"):
        await module.credentials.retrieve("synthetic-a", frozenset({"read"}))
