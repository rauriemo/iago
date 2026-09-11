"""TEST-ONLY direct adapter: in-memory counter, no accounts or external service."""

from .registry import Tool, ToolError


class FakeCounter:
    def __init__(self, options, module, account, *, credential_provider=None):
        if account not in {"synthetic-a", "synthetic-b"}:
            raise ToolError("test_account_required")
        self.module, self.account = module, account
        self.value = 0
        self.closed = False
        self.credentials = credential_provider

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def list_tools(self):
        async def read(payload, context):
            if self.closed:
                raise ToolError("adapter_closed")
            if self.credentials:
                await self.credentials.retrieve(self.account, frozenset({"counter.read"}))
            return {"value": self.value, "test_only": True}

        async def set_value(payload, context):
            if self.closed:
                raise ToolError("adapter_closed")
            if self.credentials:
                await self.credentials.retrieve(self.account, frozenset({"counter.write"}))
            self.value = payload["value"]
            return {"value": self.value, "test_only": True}

        output = {
            "type": "object",
            "properties": {
                "value": {"type": "integer", "minimum": 0, "maximum": 100},
                "test_only": {"const": True},
            },
            "required": ["value", "test_only"],
            "additionalProperties": False,
        }
        return [
            Tool(
                self.module,
                self.account,
                "read_counter",
                "Read the TEST-ONLY local counter.",
                {"type": "object", "additionalProperties": False},
                output,
                read,
            ),
            Tool(
                self.module,
                self.account,
                "set_counter",
                "Change the TEST-ONLY local counter.",
                {
                    "type": "object",
                    "properties": {"value": output["properties"]["value"]},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                output,
                set_value,
                action="write",
            ),
        ]


def create(options, module, account, *, credential_provider=None):
    return FakeCounter(options, module, account, credential_provider=credential_provider)
