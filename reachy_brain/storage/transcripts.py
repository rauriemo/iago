"""Opt-in bounded text records; generation changes reject queued writes after deletion."""

import json
import sqlite3
import time
from contextlib import closing

from jsonschema import Draft202012Validator

from reachy_brain.integrations.registry import ToolError


class Transcripts:
    def __init__(self, path, *, max_bytes=16 * 1024 * 1024, max_entries=10000):
        self.path, self.max_bytes, self.max_entries = path, max_bytes, max_entries
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS preferences(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,generation INTEGER NOT NULL)"
            )
            db.execute("INSERT OR IGNORE INTO preferences VALUES(1,0,0)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries(session TEXT,entry TEXT,role TEXT,text TEXT,kind TEXT,created REAL,PRIMARY KEY(session,entry))"
            )

            if "metadata" not in {row[1] for row in db.execute("PRAGMA table_info(entries)")}:
                db.execute("ALTER TABLE entries ADD COLUMN metadata TEXT NOT NULL DEFAULT ''")

    def state(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            enabled, generation = db.execute(
                "SELECT enabled,generation FROM preferences WHERE id=1"
            ).fetchone()
            count, size, metadata_size = db.execute(
                "SELECT count(*),coalesce(sum(length(cast(text AS blob))),0),coalesce(sum(length(cast(metadata AS blob))),0) FROM entries"
            ).fetchone()
            database_bytes = (
                db.execute("PRAGMA page_count").fetchone()[0]
                * db.execute("PRAGMA page_size").fetchone()[0]
            )
        return {
            "enabled": bool(enabled),
            "generation": generation,
            "entries": count,
            "text_bytes": size,
            "metadata_bytes": metadata_size,
            "payload_bytes": size + metadata_size,
            "max_payload_bytes": self.max_bytes,
            "max_entries": self.max_entries,
            "database_bytes": database_bytes,
            "at_capacity": count >= self.max_entries or size + metadata_size >= self.max_bytes,
        }

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ToolError("invalid_transcript_setting")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE preferences SET enabled=?,generation=generation+1 WHERE id=1 AND enabled<>?",
                (int(enabled), int(enabled)),
            )
        return self.state()

    def record(self, session, entry, role, text, kind, *, generation, metadata=None):
        if (
            not isinstance(role, str)
            or not isinstance(kind, str)
            or role not in {"user", "assistant", "system"}
            or kind not in {"speech", "typed", "gesture", "heard", "session"}
            or (role == "system") != (kind == "session")
            or (kind == "session" and (entry != "__session__" or text != ""))
            or not isinstance(text, str)
            or len(text) > 12000
            or type(generation) is not int
            or generation < 0
            or not all(
                isinstance(value, str) and 0 < len(value) <= 128 for value in (session, entry)
            )
        ):
            raise ToolError("invalid_transcript_entry")
        metadata = {} if metadata is None else metadata
        identifier = {"type": "string", "minLength": 1, "maxLength": 128}
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "epoch": {"type": "integer", "minimum": 0},
                "generated_text": {"type": "string", "maxLength": 12000},
                "interrupted": {"type": "boolean"},
                "generated_truncated": {"type": "boolean"},
                "trigger_rule_id": identifier,
                "trigger_event_id": identifier,
                "trigger_source_id": identifier,
                "evidence_truncated": {"type": "boolean"},
                "evidence_refs": {
                    "type": "array",
                    "maxItems": 32,
                    "items": {
                        "oneOf": [
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "kind": {"const": "visual"},
                                    "id": identifier,
                                    "source_id": identifier,
                                    "source_generation": {"type": "integer", "minimum": 0},
                                },
                                "required": ["kind", "id", "source_id", "source_generation"],
                            },
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "kind": {"const": "document"},
                                    "id": identifier,
                                    "project_id": identifier,
                                    "revision": identifier,
                                },
                                "required": ["kind", "id", "project_id", "revision"],
                            },
                        ]
                    },
                },
                "profile": {"enum": ["desktop", "reachy_pc", "reachy_local", "fake"]},
                "mode": {"enum": ["idle", "aware", "conversation"]},
                "session_started": {"type": "number", "minimum": 0},
                "session_ended": {"type": "number", "minimum": 0},
                "transcript_saving": {"type": "boolean"},
                "recording_generation": {"type": "integer", "minimum": 0},
                "visual_context_generation": {"type": "integer", "minimum": 0},
                "visual_generations": {
                    "type": "object",
                    "maxProperties": 8,
                    "propertyNames": identifier,
                    "additionalProperties": {"type": "integer", "minimum": 0},
                },
                "source_id": identifier,
                "source_generation": {"type": "integer", "minimum": 0},
                "question_id": identifier,
                "event_id": identifier,
                "gesture_event_ids": {"type": "array", "maxItems": 8, "items": identifier},
                "gesture_frame_ids": {"type": "array", "maxItems": 8, "items": identifier},
                "detector_version": identifier,
                "recognition_id": identifier,
                "capture_start": {"type": "number", "minimum": 0},
                "capture_end": {"type": "number", "minimum": 0},
            },
        }
        if not Draft202012Validator(schema).is_valid(metadata):
            raise ToolError("invalid_transcript_metadata")
        if kind == "session" and not {
            "session_started",
            "profile",
            "mode",
            "transcript_saving",
            "recording_generation",
            "visual_generations",
        }.issubset(metadata):
            raise ToolError("invalid_transcript_metadata")
        if "session_ended" in metadata and (
            "session_started" not in metadata
            or metadata["session_ended"] < metadata["session_started"]
        ):
            raise ToolError("invalid_transcript_metadata")
        if (
            "capture_start" in metadata
            and "capture_end" in metadata
            and metadata["capture_start"] > metadata["capture_end"]
        ):
            raise ToolError("invalid_transcript_metadata")
        try:
            encoded = (
                json.dumps(metadata, separators=(",", ":"), allow_nan=False, ensure_ascii=False)
                if metadata
                else ""
            )
        except (TypeError, ValueError):
            raise ToolError("invalid_transcript_metadata") from None
        if len(encoded.encode("utf-8")) > 65536:
            raise ToolError("invalid_transcript_metadata")
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            enabled, current = db.execute(
                "SELECT enabled,generation FROM preferences WHERE id=1"
            ).fetchone()
            if not enabled or generation != current:
                return False
            old = db.execute(
                "SELECT length(cast(text AS blob))+length(cast(metadata AS blob)) FROM entries WHERE session=? AND entry=?",
                (session, entry),
            ).fetchone()
            count, size = db.execute(
                "SELECT count(*),coalesce(sum(length(cast(text AS blob))+length(cast(metadata AS blob))),0) FROM entries"
            ).fetchone()
            if (old is None and count >= self.max_entries) or size - (old[0] if old else 0) + len(
                text.encode("utf-8")
            ) + len(encoded.encode("utf-8")) > self.max_bytes:
                raise ToolError("transcript_storage_limit")
            db.execute(
                "INSERT INTO entries(session,entry,role,text,kind,created,metadata) VALUES(?,?,?,?,?,?,?) ON CONFLICT(session,entry) DO UPDATE SET role=excluded.role,text=excluded.text,kind=excluded.kind,metadata=excluded.metadata",
                (session, entry, role, text, kind, time.time(), encoded),
            )
        return True

    def sessions(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= self.max_entries:
            raise ToolError("invalid_transcript_offset")
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT session,min(created) AS started,max(created) AS updated,sum(kind<>'session') AS entries FROM entries GROUP BY session ORDER BY started DESC,session LIMIT 100 OFFSET ?",
                    (offset,),
                )
            ]
            for row in rows:
                lifecycle = db.execute(
                    "SELECT metadata FROM entries WHERE session=? AND kind='session'",
                    (row["session"],),
                ).fetchone()
                row["lifecycle"] = json.loads(lifecycle[0]) if lifecycle else {}
                if lifecycle:
                    row["started"] = row["lifecycle"]["session_started"]
            return rows

    def entries(self, session, *, include_session=False):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [
                {**dict(row), "metadata": json.loads(row["metadata"]) if row["metadata"] else {}}
                for row in db.execute(
                    "SELECT entry,role,text,kind,created,metadata FROM entries WHERE session=? AND (kind<>'session' OR ?) ORDER BY created,entry LIMIT ?",
                    (session, include_session, self.max_entries),
                )
            ]

    def delete(self, session=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE preferences SET generation=generation+1 WHERE id=1")
            if session is None:
                db.execute("DELETE FROM entries")
            else:
                db.execute("DELETE FROM entries WHERE session=?", (session,))
        return self.state()

    def remove_entry(self, session, entry, *, generation):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT generation FROM preferences WHERE id=1").fetchone()[0]
            if generation is not None and generation != current:
                return False
            db.execute("DELETE FROM entries WHERE session=? AND entry=?", (session, entry))
        return True
