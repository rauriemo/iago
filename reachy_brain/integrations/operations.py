"""Minimal durable journal: never automatically redispatch an uncertain write."""

import sqlite3
import threading
import time
from functools import wraps
from pathlib import Path

from .registry import ToolError


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            try:
                return method(self, *args, **kwargs)
            except sqlite3.Error as exc:
                self.db.rollback()
                if getattr(exc, "sqlite_errorcode", 0) & 255 == sqlite3.SQLITE_FULL:
                    raise ToolError("journal_capacity") from None
                raise

    return call


class OperationStore:
    def __init__(
        self, path: Path, *, max_bytes=10 * 1024 * 1024, retention_days=30, clock=time.time
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.max_bytes = path, max_bytes
        self.retention = retention_days * 86400
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self._initialize(max_bytes)
        except BaseException:
            self.db.close()
            raise

    def _initialize(self, max_bytes):
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        if type(max_bytes) is not int or max_bytes < page_size * 3:
            raise ToolError("invalid_journal_capacity")
        pages = max_bytes // page_size
        actual_pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        if actual_pages > pages:
            raise ToolError("journal_capacity")
        self.db.execute(f"PRAGMA max_page_count={pages}")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, identity TEXT NOT NULL, tool TEXT NOT NULL, generation INTEGER NOT NULL, status TEXT NOT NULL, provider_ref TEXT NOT NULL DEFAULT '', updated REAL NOT NULL)"
        )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(operations)")}
        if "authorization_ref" not in columns:
            self.db.execute(
                "ALTER TABLE operations ADD COLUMN authorization_ref TEXT NOT NULL DEFAULT ''"
            )
        if "account_ref" not in columns:
            self.db.execute(
                "ALTER TABLE operations ADD COLUMN account_ref TEXT NOT NULL DEFAULT ''"
            )
        self.db.execute(
            "UPDATE operations SET status='uncertain' WHERE status IN ('dispatched','reconciling')"
        )
        self.db.execute(
            "UPDATE operations SET status='canceled-before-dispatch' WHERE status IN ('proposed','awaiting-confirmation','queued')"
        )
        self.db.commit()

    @synchronized
    def get(self, op):
        row = self.db.execute("SELECT * FROM operations WHERE id=?", (op,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def validate_identity(op, identity, tool, generation, account_ref):
        if not all(
            isinstance(value, str) and 0 < len(value) <= bound
            for value, bound in ((op, 128), (identity, 128), (tool, 256))
        ):
            raise ToolError("invalid_operation_identity")
        if type(generation) is not int or not 0 <= generation <= 2**63 - 1:
            raise ToolError("invalid_operation_identity")
        if not isinstance(account_ref, str) or (
            account_ref
            and (len(account_ref) != 64 or any(c not in "0123456789abcdef" for c in account_ref))
        ):
            raise ToolError("invalid_account_reference")

    @synchronized
    def prepare(self, op, identity, tool, generation, account_ref=""):
        self.validate_identity(op, identity, tool, generation, account_ref)
        prior = self.get(op)
        if prior:
            if (prior["identity"], prior["tool"], prior["generation"], prior["account_ref"]) != (
                identity,
                tool,
                generation,
                account_ref,
            ):
                raise ToolError("operation_conflict")
            return False
        if self.path.stat().st_size + 8192 > self.max_bytes:
            raise ToolError("journal_capacity")
        self.db.execute(
            "INSERT INTO operations(id,identity,tool,generation,status,updated,account_ref) VALUES(?,?,?,?,?,?,?)",
            (op, identity, tool, generation, "proposed", self.clock(), account_ref),
        )
        self.db.commit()
        return True

    @synchronized
    def transition(self, op, expected, status, authorization_ref=""):
        allowed = {
            "proposed": {"awaiting-confirmation", "queued"},
            "awaiting-confirmation": {"queued"},
            "queued": {"dispatched"},
        }
        if status not in allowed.get(expected, set()):
            raise ToolError("invalid_operation_transition")
        if not isinstance(authorization_ref, str):
            raise ToolError("invalid_authorization_reference")
        if (status == "queued" or authorization_ref) and (
            not isinstance(authorization_ref, str)
            or len(authorization_ref) != 64
            or any(c not in "0123456789abcdef" for c in authorization_ref)
        ):
            raise ToolError("invalid_authorization_reference")
        changed = self.db.execute(
            "UPDATE operations SET status=?,authorization_ref=CASE WHEN ?='' THEN authorization_ref ELSE ? END,updated=? WHERE id=? AND status=?",
            (status, authorization_ref, authorization_ref, self.clock(), op, expected),
        ).rowcount
        self.db.commit()
        return changed == 1

    @synchronized
    def cancel_undispatched(self, op):
        changed = self.db.execute(
            "UPDATE operations SET status='canceled-before-dispatch',updated=? WHERE id=? AND status IN ('proposed','awaiting-confirmation','queued')",
            (self.clock(), op),
        ).rowcount
        self.db.commit()
        return changed == 1

    @synchronized
    def begin(self, op, identity, tool, generation, account_ref=""):
        self.validate_identity(op, identity, tool, generation, account_ref)
        prior = self.get(op)
        if prior:
            if (prior["identity"], prior["tool"], prior["generation"], prior["account_ref"]) != (
                identity,
                tool,
                generation,
                account_ref,
            ):
                raise ToolError("operation_conflict")
            return self.transition(op, "queued", "dispatched")
        if self.path.stat().st_size + 8192 > self.max_bytes:
            raise ToolError("journal_capacity")
        try:
            self.db.execute(
                "INSERT INTO operations(id,identity,tool,generation,status,updated,account_ref) VALUES(?,?,?,?,?,?,?)",
                (op, identity, tool, generation, "dispatched", self.clock(), account_ref),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    @synchronized
    def finish(self, op, status, provider_ref=""):
        if status not in {"succeeded", "failed", "canceled-before-dispatch", "uncertain"}:
            raise ToolError("invalid_operation_status")
        # Provider references are opaque identifiers, never payloads or exception text.
        if len(str(provider_ref)) > 128:
            provider_ref = ""
        sources = (
            ("proposed", "awaiting-confirmation", "queued", "dispatched")
            if status == "canceled-before-dispatch"
            else ("dispatched", "uncertain", "reconciling")
        )
        self.db.execute(
            f"UPDATE operations SET status=?,provider_ref=?,updated=? WHERE id=? AND status IN ({','.join('?' for _ in sources)})",
            (status, str(provider_ref), self.clock(), op, *sources),
        )
        self.db.commit()

    @synchronized
    def claim_reconciliation(self, op):
        changed = self.db.execute(
            "UPDATE operations SET status='reconciling',updated=? WHERE id=? AND status='uncertain'",
            (self.clock(), op),
        ).rowcount
        self.db.commit()
        return changed == 1

    @synchronized
    def recent(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ToolError("invalid_operation_limit")
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM operations ORDER BY updated DESC LIMIT ?", (min(limit, 50),)
            )
        ]

    @synchronized
    def cleanup(self):
        removed = self.db.execute(
            "DELETE FROM operations WHERE status IN ('succeeded','failed','canceled-before-dispatch') AND updated<?",
            (self.clock() - self.retention,),
        ).rowcount
        self.db.commit()
        if removed:
            self.db.execute("VACUUM")
        return removed

    @synchronized
    def storage_status(self):
        used = self.path.stat().st_size
        unresolved = self.db.execute(
            "SELECT COUNT(*) FROM operations WHERE status NOT IN ('succeeded','failed','canceled-before-dispatch')"
        ).fetchone()[0]
        return {
            "bytes": used,
            "max_bytes": self.max_bytes,
            "retention_days": self.retention / 86400,
            "unresolved": unresolved,
            "accepting_new_writes": used + 8192 <= self.max_bytes,
        }

    @synchronized
    def close(self):
        self.db.close()
