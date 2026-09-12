"""Typed tools and code-enforced authorization, independent of conversation implementation."""

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio
from jsonschema import Draft202012Validator, ValidationError


class ToolError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class CallContext:
    session: str
    epoch: int
    valid: Callable[[], bool] = lambda: True
    capabilities: frozenset[str] = frozenset()
    operation_id: str = ""
    attachments: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    budgets: dict[str, int] = field(default_factory=dict)


@dataclass
class Connection:
    module: str
    account: str
    scopes: set[str] = field(default_factory=set)
    enabled: bool = True
    generation: int = 0
    credential_ref: str = ""
    identity: str = ""
    instance: str = field(default_factory=lambda: uuid.uuid4().hex)

    def disconnect(self):
        self.enabled = False
        self.generation += 1


@dataclass
class Tool:
    module: str
    account: str
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    handler: Callable[[dict, CallContext], Awaitable[dict]]
    action: Literal["read", "draft", "write"] = "read"
    version: str = "1"
    scopes: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    enabled: bool = True
    timeout: float = 10
    max_bytes: int = 65536
    max_items: int = 50
    reconcile_tool: str | None = None
    cancel_tool: str | None = None

    @property
    def key(self):
        return f"{self.module}__{self.account}__{self.name}"


@dataclass
class Rule:
    tool: str
    action: str
    mode: Literal["allow", "confirm", "deny"]
    constraints: dict = field(default_factory=dict)


class ActionPolicy:
    def __init__(self):
        self.rules: dict[str, Rule] = {}
        self.revision = 0

    def set(self, rule: Rule):
        if rule.action not in {"read", "draft", "write"} or rule.mode not in {
            "allow",
            "confirm",
            "deny",
        }:
            raise ToolError("invalid_action_policy")
        Draft202012Validator.check_schema(rule.constraints)
        self.rules[rule.tool] = rule
        self.revision += 1

    def decision(self, tool: Tool, payload: dict) -> str:
        rule = self.rules.get(tool.key)
        if rule is None or rule.action != tool.action:
            return "deny"
        if not Draft202012Validator(rule.constraints).is_valid(payload):
            return "deny"
        return rule.mode


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self.connections: dict[tuple[str, str], Connection] = {}

    def add_connection(self, connection: Connection):
        key = (connection.module, connection.account)
        if key in self.connections:
            raise ToolError("namespace_collision")
        self.connections[key] = connection

    def register(self, tool: Tool):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.key) or tool.key in self.tools:
            raise ToolError("namespace_collision")
        if (tool.module, tool.account) not in self.connections:
            raise ToolError("unavailable_account")
        Draft202012Validator.check_schema(tool.input_schema)
        Draft202012Validator.check_schema(tool.output_schema)
        self.tools[tool.key] = tool

    def available(self, tool: Tool, context: CallContext) -> Connection:
        connection = self.connections[(tool.module, tool.account)]
        if not tool.enabled or not connection.enabled:
            raise ToolError("disabled")
        if not tool.scopes <= connection.scopes or not tool.capabilities <= context.capabilities:
            raise ToolError("missing_capability")
        if not context.valid():
            raise ToolError("canceled")
        return connection

    def discover(self, policy: ActionPolicy, context: CallContext) -> list[dict]:
        result = []
        for tool in self.tools.values():
            try:
                self.available(tool, context)
            except ToolError:
                continue
            rule = policy.rules.get(tool.key)
            if not rule or rule.mode == "deny" or rule.action != tool.action:
                continue
            result.append(
                {
                    "type": "function",
                    "name": tool.key,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": False,
                }
            )
        return result


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def bounded(value: Any, max_bytes: int, max_items: int) -> None:
    """Check breadth/depth before JSON serialization; transports additionally bound wire input."""
    remaining = max_bytes

    def walk(item, depth=0):
        nonlocal remaining
        if depth > 16:
            raise ToolError("result_limit")
        if isinstance(item, dict):
            if len(item) > max_items:
                raise ToolError("result_limit")
            for key, val in item.items():
                walk(str(key), depth + 1)
                walk(val, depth + 1)
        elif isinstance(item, (list, tuple)):
            if len(item) > max_items:
                raise ToolError("result_limit")
            for val in item:
                walk(val, depth + 1)
        elif isinstance(item, str):
            remaining -= len(item.encode("utf-8"))
        else:
            remaining -= 16
        if remaining < 0:
            raise ToolError("result_limit")

    walk(value)
    if len(json.dumps(value, allow_nan=False).encode()) > max_bytes:
        raise ToolError("result_limit")


class ToolExecutor:
    def __init__(self, registry, policy, operations, *, concurrency=4, clock=time.time):
        self.registry, self.policy, self.operations = registry, policy, operations
        self.clock = clock
        self.slots = asyncio.Semaphore(concurrency)
        self.pending: dict[str, dict] = {}
        self.diagnostics: list[dict] = []
        self.executions = set()
        self.max_pending = concurrency + 32
        self.closed = False
        self.cancellations = set()
        self.timing_sequence = 0
        self.timing_samples = deque(maxlen=64)

    async def close(self):
        self.closed = True
        for operation in list(self.pending):
            self.drop_proposal(operation)
        tasks = self.executions - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            with anyio.CancelScope(shield=True):
                done, pending = await asyncio.wait(tasks, timeout=10)
                for task in done:
                    if not task.cancelled():
                        task.exception()
                if pending:
                    raise ToolError("integration_shutdown_timeout")

        if self.cancellations:
            with anyio.CancelScope(shield=True):
                done, pending = await asyncio.wait(self.cancellations, timeout=10)
                if pending:
                    raise ToolError("journal_shutdown_timeout")

    def drop_proposal(self, operation):
        proposal = self.pending.pop(operation, None)
        if proposal and proposal.get("journaled"):
            task = asyncio.create_task(self.journal(self.operations.cancel_undispatched, operation))
            self.cancellations.add(task)

            def completed(done):
                self.cancellations.discard(done)
                if not done.cancelled() and done.exception():
                    self.diagnostics.append(
                        {"tool": proposal["tool"], "error": "proposal_cancel_journal_failed"}
                    )
                    del self.diagnostics[:-100]

            task.add_done_callback(completed)

    async def journal(self, method, *args, observed=None):
        work = asyncio.create_task(asyncio.to_thread(method, *args))
        try:
            result = await asyncio.shield(work)
        except asyncio.CancelledError:
            # A committed SQLite write cannot be canceled by canceling its waiter.
            # Resolve it off-thread before classifying dispatch; local Stop never
            # waits for this task to finish.
            with anyio.CancelScope(shield=True):
                while not work.done():
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        continue
                result = work.result()
                if observed:
                    observed(result)
            raise
        if observed:
            observed(result)
        return result

    def _binding(self, tool, payload, connection, context):
        return digest(
            {
                "operation": context.operation_id,
                "tool": tool.key,
                "version": tool.version,
                "account_generation": connection.generation,
                "account_ref": self.account_ref(connection),
                "payload": payload,
                "policy_revision": self.policy.revision,
                "session": context.session,
                "epoch": context.epoch,
            }
        )

    @staticmethod
    def account_ref(connection):
        return digest(
            {
                "module": connection.module,
                "account": connection.account,
                "identity": connection.identity or connection.instance,
            }
        )

    def same_account(self, connection, operation):
        return operation.get("account_ref") == self.account_ref(connection) and (
            bool(connection.identity) or connection.generation == operation["generation"]
        )

    async def propose(self, key, payload, context) -> dict:
        if self.closed or len(self.executions) >= self.max_pending:
            raise ToolError("executor_unavailable")
        self._expire()
        if context.operation_id in self.pending:
            raise ToolError("proposal_already_pending")
        if len(self.pending) + len(self.cancellations) >= 50:
            raise ToolError("confirmation_limit")
        execution = asyncio.current_task()
        self.executions.add(execution)
        prepared = [False]
        proposal = None
        try:
            result = self._proposal(key, payload, context)
            proposal = self.pending[context.operation_id]
            tool = self.registry.tools[key]
            connection = self.registry.available(tool, context)
            if tool.action == "write":
                identity = digest(
                    {
                        "tool": key,
                        "version": tool.version,
                        "generation": connection.generation,
                        "payload": payload,
                    }
                )
                if not await self.journal(
                    self.operations.prepare,
                    context.operation_id,
                    identity,
                    key,
                    connection.generation,
                    self.account_ref(connection),
                    observed=lambda accepted: prepared.__setitem__(0, accepted),
                ):
                    raise ToolError("operation_already_exists")
                proposal["journaled"] = True
                if not await self.journal(
                    self.operations.transition,
                    context.operation_id,
                    "proposed",
                    "awaiting-confirmation",
                ):
                    raise ToolError("canceled")
            self.registry.available(tool, context)
            if (
                self.pending.get(context.operation_id) is not proposal
                or self.clock() >= proposal["expires"]
            ):
                raise ToolError("canceled")
            if self._binding(tool, payload, connection, context) != result["binding"]:
                raise ToolError("authorization_changed")
            return result
        except BaseException:
            if proposal is not None and self.pending.get(context.operation_id) is proposal:
                self.pending.pop(context.operation_id, None)
            if prepared[0]:
                await self.journal(self.operations.cancel_undispatched, context.operation_id)
            raise
        finally:
            self.executions.discard(execution)

    def _proposal(self, key, payload, context) -> dict:
        tool = self.registry.tools.get(key)
        if tool is None:
            raise ToolError("unknown_tool")
        conn = self.registry.available(tool, context)
        if not context.operation_id:
            raise ToolError("missing_operation_id")
        bounded(payload, tool.max_bytes, tool.max_items)
        if not Draft202012Validator(tool.input_schema).is_valid(payload):
            raise ToolError("invalid_input")
        if self.policy.decision(tool, payload) != "confirm":
            raise ToolError("confirmation_not_allowed")
        if len(self.pending) >= 50:
            self._expire()
            if len(self.pending) >= 50:
                raise ToolError("confirmation_limit")
        binding = self._binding(tool, payload, conn, context)
        self.pending[context.operation_id] = {
            "binding": binding,
            "expires": self.clock() + 60,
            "approved": False,
            "context": context,
            "tool": key,
            "payload": json.loads(json.dumps(payload)),
            "account": tool.account,
        }
        return {
            "operation_id": context.operation_id,
            "tool": key,
            "account": tool.account,
            "payload": payload,
            "binding": binding,
            "expires": self.clock() + 60,
        }

    def _expire(self):
        for op, proposal in list(self.pending.items()):
            if proposal["expires"] <= self.clock() or not proposal["context"].valid():
                self.drop_proposal(op)

    def confirm(self, operation_id: str, binding: str, *, input_kind: str):
        self._expire()
        proposal = self.pending.get(operation_id)
        # Speech confirmations must first be bound by the controller; gestures never enter here.
        if input_kind not in {"action_ui", "bound_speech"} or not proposal:
            raise ToolError("invalid_confirmation")
        if proposal["binding"] != binding or proposal["approved"]:
            raise ToolError("invalid_confirmation")
        proposal["approved"] = True

    def cancel_pending(self, session: str, epoch: int):
        for op, proposal in list(self.pending.items()):
            ctx = proposal["context"]
            if ctx.session == session and ctx.epoch == epoch:
                self.drop_proposal(op)

    async def cancel_undispatched(self, operation_id):
        """Conditional local cancellation; never claim to undo provider dispatch."""
        if self.closed:
            raise ToolError("executor_closed")
        if len(self.executions) >= self.max_pending:
            raise ToolError("integration_queue_full")
        task = asyncio.current_task()
        self.executions.add(task)
        try:
            if not await self.journal(self.operations.get, operation_id):
                raise ToolError("unknown_operation")
            self.drop_proposal(operation_id)
            canceled = await self.journal(self.operations.cancel_undispatched, operation_id)
            record = await self.journal(self.operations.get, operation_id)
            return {
                "operation": record,
                "canceled": canceled or record["status"] == "canceled-before-dispatch",
            }
        finally:
            self.executions.discard(task)

    def timing_snapshot(self):
        return {
            "samples": list(self.timing_samples),
            "total": self.timing_sequence,
            "sample_limit": 64,
            "scope": "Backend execute calls including validation, queue and journal waits; excludes proposal/user-confirmation waiting, reconciliation calls and physical effects. No tool/account/payload identifiers.",
        }

    async def execute(self, key: str, payload: dict, context: CallContext) -> dict:
        started, outcome = time.perf_counter(), "error"
        tool = self.registry.tools.get(key)
        action = tool.action if tool and tool.action in {"read", "draft", "write"} else "unknown"
        try:
            result = await self._execute(key, payload, context)
            outcome = "ok" if result.get("status") == "ok" else "unsuccessful"
            return result
        except asyncio.CancelledError:
            outcome = "canceled"
            raise
        finally:
            self.timing_sequence += 1
            self.timing_samples.append(
                {
                    "sequence": self.timing_sequence,
                    "operation": action,
                    "outcome": outcome,
                    "seconds": max(0, time.perf_counter() - started),
                }
            )

    async def _execute(self, key: str, payload: dict, context: CallContext) -> dict:
        if self.closed:
            return {"status": "executor_closed", "operation_id": context.operation_id}
        if len(self.executions) >= self.max_pending:
            return {"status": "tool_queue_full", "operation_id": context.operation_id}
        execution = asyncio.current_task()
        self.executions.add(execution)
        started = self.clock()
        tool = self.registry.tools.get(key)
        dispatched = False
        claimed = [False]
        prepared = [False]
        try:
            if tool is None:
                raise ToolError("unknown_tool")
            conn = self.registry.available(tool, context)
            generation = conn.generation
            bounded(payload, tool.max_bytes, tool.max_items)
            if not Draft202012Validator(tool.input_schema).is_valid(payload):
                raise ToolError("invalid_input")
            async with asyncio.timeout(tool.timeout):
                self.registry.available(tool, context)
                if conn.generation != generation:
                    raise ToolError("canceled")
                decision = self.policy.decision(tool, payload)
                if decision == "deny":
                    raise ToolError("denied")
                binding = self._binding(tool, payload, conn, context)
                # An operation's stable payload identity excludes current turn/policy revisions.
                identity = digest(
                    {
                        "tool": key,
                        "version": tool.version,
                        "generation": generation,
                        "payload": payload,
                    }
                )
                if tool.action == "write":
                    if not context.operation_id:
                        raise ToolError("missing_operation_id")
                    prior = await self.journal(self.operations.get, context.operation_id)
                    if prior:
                        if prior["identity"] != identity or prior[
                            "account_ref"
                        ] != self.account_ref(conn):
                            raise ToolError("operation_conflict")
                        if prior["status"] != "awaiting-confirmation":
                            return {
                                "status": prior["status"],
                                "operation_id": context.operation_id,
                                "provider_ref": prior["provider_ref"],
                                "duplicate": True,
                            }
                if decision == "confirm":
                    self._expire()
                    proposal = self.pending.get(context.operation_id)
                    if not proposal or not proposal["approved"] or proposal["binding"] != binding:
                        raise ToolError("confirmation_required")
                    self.pending.pop(context.operation_id)
                if tool.action == "write":
                    if prior and prior["status"] == "awaiting-confirmation":
                        self.pending.pop(context.operation_id, None)
                        if not await self.journal(
                            self.operations.transition,
                            context.operation_id,
                            "awaiting-confirmation",
                            "queued",
                            binding,
                            observed=lambda accepted: prepared.__setitem__(0, accepted),
                        ):
                            raise ToolError("canceled-before-dispatch")
                    else:
                        if not await self.journal(
                            self.operations.prepare,
                            context.operation_id,
                            identity,
                            key,
                            generation,
                            self.account_ref(conn),
                            observed=lambda accepted: prepared.__setitem__(0, accepted),
                        ):
                            prior = await self.journal(self.operations.get, context.operation_id)
                            return {
                                "status": prior["status"],
                                "operation_id": context.operation_id,
                                "provider_ref": prior["provider_ref"],
                                "duplicate": True,
                            }
                        if not await self.journal(
                            self.operations.transition,
                            context.operation_id,
                            "proposed",
                            "queued",
                            binding,
                        ):
                            raise ToolError("canceled-before-dispatch")
                async with self.slots:
                    self.registry.available(tool, context)
                    if (
                        conn.generation != generation
                        or self._binding(tool, payload, conn, context) != binding
                    ):
                        raise ToolError("authorization_changed")
                    if tool.action == "write":
                        # Claim the persisted queue entry before any provider invocation.
                        if not await self.journal(
                            self.operations.begin,
                            context.operation_id,
                            identity,
                            key,
                            generation,
                            self.account_ref(conn),
                            observed=lambda accepted: claimed.__setitem__(0, accepted),
                        ):
                            record = await self.journal(self.operations.get, context.operation_id)
                            if record and record["status"] == "canceled-before-dispatch":
                                raise ToolError("canceled-before-dispatch")
                            raise ToolError("duplicate_inflight")
                    self.registry.available(tool, context)
                    if (
                        conn.generation != generation
                        or self._binding(tool, payload, conn, context) != binding
                    ):
                        raise ToolError("authorization_changed")
                    dispatched = True
                    output = await tool.handler(payload, context)
                    bounded(output, tool.max_bytes, tool.max_items)
                    Draft202012Validator(tool.output_schema).validate(output)
                    if tool.action == "write":
                        await self.journal(
                            self.operations.finish,
                            context.operation_id,
                            "succeeded",
                            output.get("provider_ref", ""),
                        )
                    self.registry.available(tool, context)
                    if conn.generation != generation:
                        raise ToolError("canceled")
                    return {"status": "ok", "result": output, "operation_id": context.operation_id}
        except asyncio.CancelledError:
            if claimed[0] or prepared[0]:
                await self.journal(
                    self.operations.finish,
                    context.operation_id,
                    "uncertain" if dispatched else "canceled-before-dispatch",
                )
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, ToolError)
                else (
                    "timeout"
                    if isinstance(exc, TimeoutError)
                    else "invalid_result"
                    if isinstance(exc, ValidationError)
                    else "unavailable"
                )
            )
            if dispatched and tool and tool.action == "write":
                prior = await self.journal(self.operations.get, context.operation_id)
                if prior and prior["status"] != "succeeded":
                    await self.journal(self.operations.finish, context.operation_id, "uncertain")
                    code = "uncertain"
            elif claimed[0] or prepared[0]:
                await self.journal(
                    self.operations.finish, context.operation_id, "canceled-before-dispatch"
                )
            return {"status": code, "operation_id": context.operation_id}
        finally:
            self.executions.discard(execution)
            self.diagnostics.append({"tool": key, "seconds": self.clock() - started})
            del self.diagnostics[:-100]

    async def request_cancellation(
        self, operation_id: str, context: CallContext, *, prepare=False
    ) -> dict:
        if self.closed:
            raise ToolError("executor_closed")
        if not context.valid():
            raise ToolError("canceled")
        if not context.operation_id or context.operation_id == operation_id:
            raise ToolError("distinct_cancellation_operation_required")
        original = await self.journal(self.operations.get, operation_id)
        if not original:
            raise ToolError("unknown_operation")
        tool = self.registry.tools.get(original["tool"])
        cancel = self.registry.tools.get(tool.cancel_tool) if tool else None
        if (
            not cancel
            or cancel.action != "write"
            or (cancel.module, cancel.account) != (tool.module, tool.account)
        ):
            raise ToolError("provider_cancellation_unavailable")
        connection = self.registry.available(cancel, context)
        if not self.same_account(connection, original):
            raise ToolError("provider_cancellation_unavailable")
        if original["status"] not in {"dispatched", "uncertain"}:
            raise ToolError("operation_not_cancelable")
        payload = {"operation_id": operation_id}
        if prepare:
            return await self.propose(cancel.key, payload, context)
        # Cancellation is another external write. Its receipt does not prove that
        # the original write was undone, so never rewrite the original journal here.
        return await self.execute(cancel.key, payload, context)

    async def reconcile(self, operation_id: str, context: CallContext) -> dict:
        if self.closed:
            raise ToolError("executor_closed")
        if len(self.executions) >= self.max_pending:
            raise ToolError("tool_queue_full")
        execution = asyncio.current_task()
        self.executions.add(execution)
        claimed = [False]
        try:
            op = await self.journal(self.operations.get, operation_id)
            if not op:
                raise ToolError("unknown_operation")
            tool = self.registry.tools.get(op["tool"])
            if not tool:
                raise ToolError("reconciliation_unavailable")
            if not context.valid():
                raise ToolError("canceled")
            # Active dispatch belongs to its executor; terminal outcomes need no lookup.
            if op["status"] != "uncertain":
                return op
            lookup = self.registry.tools.get(tool.reconcile_tool)
            payload = {"operation_id": operation_id}
            if (
                not lookup
                or lookup.action != "read"
                or (lookup.module, lookup.account) != (tool.module, tool.account)
            ):
                raise ToolError("reconciliation_unavailable")
            connection = self.registry.available(lookup, context)
            if not self.same_account(connection, op):
                raise ToolError("reconciliation_unavailable")
            if self.policy.decision(lookup, payload) != "allow":
                raise ToolError("reconciliation_not_authorized")
            if not Draft202012Validator(lookup.input_schema).is_valid(payload):
                raise ToolError("invalid_reconciliation_input")
            async with asyncio.timeout(min(tool.timeout, lookup.timeout)), self.slots:
                if not await self.journal(
                    self.operations.claim_reconciliation,
                    operation_id,
                    observed=lambda accepted: claimed.__setitem__(0, accepted),
                ):
                    return await self.journal(self.operations.get, operation_id)
                connection = self.registry.available(lookup, context)
                if not self.same_account(connection, op):
                    raise ToolError("reconciliation_unavailable")
                if self.policy.decision(lookup, payload) != "allow":
                    raise ToolError("reconciliation_not_authorized")
                result = await lookup.handler(payload, context)
                connection = self.registry.available(lookup, context)
                if not self.same_account(connection, op):
                    raise ToolError("reconciliation_unavailable")
                bounded(result, lookup.max_bytes, lookup.max_items)
                if not Draft202012Validator(lookup.output_schema).is_valid(result):
                    raise ToolError("invalid_reconciliation_result")
                # Only normalized status/reference metadata belongs in the journal.
                result = {key: result[key] for key in ("status", "provider_ref") if key in result}
                bounded(result, min(tool.max_bytes, 4096), min(tool.max_items, 10))
                schema = {
                    "type": "object",
                    "properties": {
                        "status": {"enum": ["succeeded", "failed", "uncertain"]},
                        "provider_ref": {"type": "string", "maxLength": 128},
                    },
                    "required": ["status"],
                    "additionalProperties": False,
                }
                if not Draft202012Validator(schema).is_valid(result):
                    raise ToolError("invalid_reconciliation_result")
                await self.journal(
                    self.operations.finish,
                    operation_id,
                    result["status"],
                    result.get("provider_ref", ""),
                )
            return await self.journal(self.operations.get, operation_id)
        finally:
            try:
                if claimed[0]:
                    # A canceled, failed or unavailable lookup leaves truthful uncertainty.
                    # Terminal journal outcomes are immutable, so this cannot erase success.
                    await self.journal(self.operations.finish, operation_id, "uncertain")
            finally:
                self.executions.discard(execution)
