"""Backend-only credential interface with generation-aware refresh/disconnect."""

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from .registry import ToolError


@dataclass
class Credential:
    token: SecretStr
    scopes: frozenset[str]
    expires: float


class CredentialProvider(Protocol):
    async def retrieve(self, account: str, scopes: frozenset[str]) -> SecretStr: ...
    async def disconnect(self, account: str) -> bool: ...


def configured_credentials(config):
    credential = config.get("credentials")
    if credential is None:
        return None
    if (
        not isinstance(credential, dict)
        or set(credential) != {"token_environment", "scopes"}
        or not isinstance(credential["token_environment"], str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", credential["token_environment"])
        or not isinstance(credential["scopes"], list)
        or len(credential["scopes"]) > 100
        or any(
            not isinstance(scope, str) or not 0 < len(scope) <= 128
            for scope in credential["scopes"]
        )
    ):
        raise ToolError("invalid_credential_configuration")
    return LocalCredentials({config["account"]: credential})


class LocalCredentials:
    def __init__(
        self,
        config: dict[str, dict],
        *,
        refresh: Callable[[str], Awaitable[Credential]] | None = None,
        revoke: Callable[[str], Awaitable[bool]] | None = None,
        clock=time.time,
    ):
        self.config, self.refresh, self.revoke, self.clock = config, refresh, revoke, clock
        self.cache: dict[str, Credential] = {}
        self.generations: dict[str, int] = {}
        self.disconnected: set[str] = set()
        self.locks: dict[str, asyncio.Lock] = {}

    async def retrieve(self, account, scopes):
        if account not in self.config or account in self.disconnected:
            raise ToolError("account_disconnected")
        async with self.locks.setdefault(account, asyncio.Lock()):
            generation = self.generations.get(account, 0)
            cfg = self.config[account]
            if not scopes <= set(cfg.get("scopes", [])):
                raise ToolError("missing_scope")
            value = self.cache.get(account)
            if not value or value.expires <= self.clock() + 30:
                if self.refresh:
                    async with asyncio.timeout(10):
                        value = await self.refresh(account)
                else:
                    token = os.getenv(cfg["token_environment"], "")
                    if not token:
                        raise ToolError("missing_credential")
                    value = Credential(SecretStr(token), frozenset(cfg["scopes"]), float("inf"))
                if account in self.disconnected or generation != self.generations.get(account, 0):
                    raise ToolError("account_disconnected")
                if not value.token.get_secret_value() or value.expires <= self.clock():
                    raise ToolError("credential_expired")
                self.cache[account] = value
            if not scopes <= value.scopes:
                raise ToolError("missing_scope")
            return value.token

    async def disconnect(self, account):
        self.generations[account] = self.generations.get(account, 0) + 1
        self.disconnected.add(account)
        self.cache.pop(account, None)
        if self.revoke:
            try:
                async with asyncio.timeout(10):
                    return await self.revoke(account)
            except (TimeoutError, OSError):
                return False
        return True
