"""TEST ONLY. Local synthetic calendar data; no personal accounts or network providers."""

import argparse
import asyncio
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server import MCPServer


def make_server(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute(
        "CREATE TABLE IF NOT EXISTS events (operation TEXT PRIMARY KEY, account TEXT, title TEXT, start TEXT)"
    )
    db.commit()
    db.execute(
        "CREATE TABLE IF NOT EXISTS pending_events(operation TEXT PRIMARY KEY,account TEXT,title TEXT,start TEXT,state TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS cancellation_requests(request TEXT PRIMARY KEY,account TEXT,target TEXT,outcome TEXT)"
    )
    db.commit()
    db.execute(
        "CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY CHECK(id=1),resource TEXT NOT NULL)"
    )
    db.execute("INSERT OR IGNORE INTO identity VALUES(1,?)", (uuid.uuid4().hex,))
    db.commit()

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            db.close()

    server = MCPServer(
        "Iago TEST-ONLY calendar", version="1.0", log_level="WARNING", lifespan=lifespan
    )

    def check(account):
        if account not in {"synthetic-a", "synthetic-b"}:
            raise ValueError("Only synthetic accounts are supported")

    @server.tool(structured_output=True)
    async def connection_identity(account: str) -> dict[str, Any]:
        """Identity of this TEST-ONLY store/account; no real identity provider is contacted."""
        check(account)
        resource = db.execute("SELECT resource FROM identity WHERE id=1").fetchone()[0]
        return {"issuer": "iago-test-calendar", "resource": resource, "account": account}

    @server.tool(structured_output=True)
    async def list_events(account: str, limit: int = 20) -> dict[str, Any]:
        """Read isolated fake calendar events; no real calendar is connected."""
        check(account)
        if not 1 <= limit <= 50:
            raise ValueError("limit must be 1..50")
        rows = db.execute(
            "SELECT operation,title,start FROM events WHERE account=? ORDER BY start LIMIT ?",
            (account, limit),
        ).fetchall()
        return {
            "events": [{"provider_ref": r[0], "title": r[1], "start": r[2]} for r in rows],
            "test_only": True,
        }

    @server.tool(structured_output=True)
    async def draft_event(account: str, title: str, start: str) -> dict[str, Any]:
        """Prepare a local draft without creating an event."""
        check(account)
        if len(title) > 100 or len(start) > 50:
            raise ValueError("Input too long")
        return {"account": account, "title": title, "start": start, "test_only": True}

    @server.tool(structured_output=True)
    async def create_event(
        account: str,
        operation_id: str,
        title: str,
        start: str,
        delay_seconds: float = 0,
        lose_response: bool = False,
        prepare_delay_seconds: float = 0,
    ) -> dict[str, Any]:
        """Create in the fake store. Iago host must authorize every write."""
        check(account)
        if (
            not operation_id
            or len(operation_id) > 128
            or len(title) > 100
            or len(start) > 50
            or not 0 <= delay_seconds <= 30
            or not 0 <= prepare_delay_seconds <= 30
        ):
            raise ValueError("Invalid fake event")
        prior = db.execute(
            "SELECT account,title,start FROM events WHERE operation=?", (operation_id,)
        ).fetchone()
        if prior and prior != (account, title, start):
            raise ValueError("Idempotency conflict")
        if not prior:
            pending = db.execute(
                "SELECT account,title,start,state FROM pending_events WHERE operation=?",
                (operation_id,),
            ).fetchone()
            if pending:
                if pending[:3] != (account, title, start):
                    raise ValueError("Idempotency conflict")
                raise ValueError("Pending or canceled operation; reconcile without replay")
            db.execute(
                "INSERT INTO pending_events VALUES(?,?,?,?,?)",
                (operation_id, account, title, start, "queued"),
            )
            db.commit()
            await asyncio.sleep(prepare_delay_seconds)
            state = db.execute(
                "SELECT state FROM pending_events WHERE operation=?", (operation_id,)
            ).fetchone()[0]
            if state == "canceled":
                raise ValueError("Synthetic provider cancellation before commit")
        db.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?)", (operation_id, account, title, start)
        )
        db.commit()
        await asyncio.sleep(delay_seconds)
        if lose_response:
            raise ValueError("Synthetic response loss after commit; reconcile")
        return {"provider_ref": operation_id, "test_only": True}

    @server.tool(structured_output=True)
    async def request_cancel(account: str, operation_id: str, request_id: str) -> dict[str, Any]:
        """TEST ONLY: cancel a pending synthetic create; committed events are never deleted."""
        check(account)
        if not all(0 < len(value) <= 128 for value in (operation_id, request_id)):
            raise ValueError("Invalid cancellation ID")
        prior = db.execute(
            "SELECT account,target,outcome FROM cancellation_requests WHERE request=?",
            (request_id,),
        ).fetchone()
        if prior:
            if prior[:2] != (account, operation_id):
                raise ValueError("Idempotency conflict")
            outcome = prior[2]
        else:
            committed = db.execute(
                "SELECT 1 FROM events WHERE operation=? AND account=?", (operation_id, account)
            ).fetchone()
            pending = db.execute(
                "SELECT state FROM pending_events WHERE operation=? AND account=?",
                (operation_id, account),
            ).fetchone()
            outcome = "too_late" if committed else "canceled" if pending else "unavailable"
            if pending and not committed:
                db.execute(
                    "UPDATE pending_events SET state='canceled' WHERE operation=? AND account=?",
                    (operation_id, account),
                )
            db.execute(
                "INSERT INTO cancellation_requests VALUES(?,?,?,?)",
                (request_id, account, operation_id, outcome),
            )
            db.commit()
        return {"provider_ref": request_id, "outcome": outcome, "test_only": True}

    @server.tool(structured_output=True)
    async def operation_status(account: str, operation_id: str) -> dict[str, Any]:
        """Reconcile fake operation state without repeating the write."""
        check(account)
        row = db.execute(
            "SELECT operation FROM events WHERE account=? AND operation=?", (account, operation_id)
        ).fetchone()
        cancellation = db.execute(
            "SELECT request FROM cancellation_requests WHERE request=? AND account=?",
            (operation_id, account),
        ).fetchone()
        pending = db.execute(
            "SELECT state FROM pending_events WHERE operation=? AND account=?",
            (operation_id, account),
        ).fetchone()
        return {
            "status": "succeeded"
            if row or cancellation
            else "uncertain"
            if pending and pending[0] == "queued"
            else "failed",
            "provider_ref": row[0] if row else cancellation[0] if cancellation else "",
            "test_only": True,
        }

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    server = make_server(args.data)
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
