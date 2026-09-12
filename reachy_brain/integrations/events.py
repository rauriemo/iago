"""Host-side future event ingress; no webhook, scheduler or authorization channel."""

import math
from collections import OrderedDict
from dataclasses import asdict

from reachy_brain.behavior.engine import Event

from .registry import ToolError, bounded


class IntegrationEventSource:
    def __init__(self, registry, engine):
        self.registry, self.engine = registry, engine
        self.latest = OrderedDict()
        engine.event_validator = self.valid

    def valid(self, event, now=None):
        data = asdict(event) if isinstance(event, Event) else event
        if not data["source"].startswith("integration:"):
            return True
        details = data["details"]
        connection = self.registry.connections.get((details.get("module"), details.get("account")))
        return bool(
            connection
            and connection.enabled
            and data["source"] == f"integration:{connection.module}:{connection.account}"
            and data["generation"] == connection.generation
            and details.get("connection_instance") == connection.instance
            and (now is None or 0 <= now - data["captured"] <= 5)
        )

    def offer(self, module, account, *, generation, event_id, kind, occurred, now, payload):
        if (
            not isinstance(module, str)
            or not isinstance(account, str)
            or not 1 <= len(module) <= 64
            or not 1 <= len(account) <= 64
            or type(generation) is not int
            or not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 128
            or not isinstance(kind, str)
            or not 1 <= len(kind) <= 100
            or not isinstance(payload, dict)
            or not all(type(v) in (int, float) and math.isfinite(v) for v in (occurred, now))
        ):
            raise ToolError("invalid_integration_event")
        bounded(payload, 4096, 20)
        connection = self.registry.connections.get((module, account))
        if not connection or not connection.enabled or connection.generation != generation:
            return "account_unavailable"
        event = Event(
            event_id,
            f"integration:{module}:{account}",
            kind,
            occurred,
            1,
            generation=generation,
            detector="integration-ingress-v1",
            details={
                "module": module,
                "account": account,
                "connection_instance": connection.instance,
                "received": now,
                "payload": payload,
            },
        )
        if not 0 <= now - occurred <= 5:
            return self.engine._record(event, "expired", now)
        if (event.source, generation, event_id) in self.engine.seen:
            return self.engine.offer(event, now=now)
        key = (connection.instance, generation)
        if occurred < self.latest.get(key, -math.inf):
            return self.engine._record(event, "out_of_order", now)
        self.latest[key] = occurred
        self.latest.move_to_end(key)
        while len(self.latest) > 1000:
            self.latest.popitem(last=False)
        return self.engine.offer(event, now=now)
