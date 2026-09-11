"""Account-bound backend bearer retrieval; credentials never become tool arguments."""

import re

import httpx2

from .registry import ToolError


class AccountBearer(httpx2.Auth):
    def __init__(self, provider, account, scopes, endpoint):
        self.provider, self.account, self.scopes = provider, account, frozenset(scopes)
        url = httpx2.URL(endpoint)
        self.origin = (url.scheme, url.host, url.port)

    async def async_auth_flow(self, request):
        url = request.url
        if (url.scheme, url.host, url.port) != self.origin:
            raise ToolError("credential_origin_mismatch")
        token = await self.provider.retrieve(self.account, self.scopes)
        value = token.get_secret_value()
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
            raise ToolError("invalid_bearer_credential")
        request.headers["Authorization"] = "Bearer " + value
        yield request
