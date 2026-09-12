import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from reachy_brain.integrations.registry import ToolError


class Notes:
    def __init__(self, path: Path, *, max_bytes=8 * 1024 * 1024, max_notes=1000):
        self.path, self.max_bytes, self.max_notes = path, max_bytes, max_notes
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,text TEXT,created REAL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS note_store(id INTEGER PRIMARY KEY CHECK(id=1),identity TEXT NOT NULL)"
            )
            db.execute("INSERT OR IGNORE INTO note_store VALUES(1,?)", (uuid.uuid4().hex,))
            self.identity = db.execute("SELECT identity FROM note_store WHERE id=1").fetchone()[0]
            try:
                if uuid.UUID(self.identity).hex != self.identity:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ToolError("invalid_notes_identity") from None

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            if type(self.max_bytes) is not int or self.max_bytes < page_size:
                raise ToolError("invalid_notes_capacity")
            pages = self.max_bytes // page_size
            if db.execute("PRAGMA page_count").fetchone()[0] > pages:
                raise ToolError("notes_storage_limit")
            if db.execute(f"PRAGMA max_page_count={pages}").fetchone()[0] > pages:
                raise ToolError("notes_storage_limit")
            with db:
                yield db
        except sqlite3.Error as exc:
            db.rollback()
            if getattr(exc, "sqlite_errorcode", 0) & 255 == sqlite3.SQLITE_FULL:
                raise ToolError("notes_storage_limit") from None
            raise
        finally:
            db.close()

    def operation_note_id(self, operation_id):
        if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 128:
            raise ToolError("invalid_note_operation")
        return uuid.uuid5(uuid.UUID(self.identity), operation_id).hex

    def operation_status(self, operation_id):
        note_id = self.operation_note_id(operation_id)
        with self.connection() as db:
            exists = db.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone()
        # Absence cannot distinguish not-yet-committed, failed, deleted or restored data.
        return (
            {"status": "succeeded", "provider_ref": note_id} if exists else {"status": "uncertain"}
        )

    def save(self, text, *, operation_id=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ToolError("invalid_note")
        note_id = (
            self.operation_note_id(operation_id) if operation_id is not None else uuid.uuid4().hex
        )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT text FROM notes WHERE id=?", (note_id,)).fetchone()
            if existing:
                if existing["text"] != text:
                    raise ToolError("note_operation_conflict")
                return note_id
            count, size = db.execute(
                "SELECT count(*),coalesce(sum(length(cast(text AS blob))),0) FROM notes"
            ).fetchone()
            if count >= self.max_notes or size + len(text.encode("utf-8")) > self.max_bytes:
                raise ToolError("notes_storage_limit")
            db.execute("INSERT INTO notes VALUES(?,?,?)", (note_id, text, time.time()))
        return note_id

    def list(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= 1000000:
            raise ToolError("invalid_note_offset")
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,substr(text,1,12000) AS text,created FROM notes ORDER BY created DESC,id LIMIT 100 OFFSET ?",
                    (offset,),
                )
            ]

    def count(self):
        with self.connection() as db:
            return db.execute("SELECT count(*) FROM notes").fetchone()[0]

    def export(self, note_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT substr(text,1,12001) AS text FROM notes WHERE id=?", (note_id,)
            ).fetchone()
        if row is None:
            raise ToolError("note_not_found")
        if len(row["text"]) > 12000:
            raise ToolError("note_export_limit")
        return row["text"].encode("utf-8")

    def delete(self, note_id):
        with self.connection() as db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("DELETE FROM notes WHERE id=?", (note_id,))
