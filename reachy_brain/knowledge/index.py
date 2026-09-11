import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from reachy_brain.integrations.registry import ToolError
from reachy_brain.knowledge.process import capture_parser
from reachy_brain.knowledge.staging import StagingBudget
from reachy_brain.knowledge.timing import ProjectTimings, timed

EXCLUDED = {
    ".git",
    ".venv",
    "node_modules",
    "vendor",
    "__pycache__",
    "build",
    "dist",
    ".cache",
    ".ssh",
    ".aws",
    ".azure",
    "credentials",
    "credential-store",
}


class ProjectIndex:
    def __init__(self, path: Path, *, max_bytes=2 * 1024**3, max_files=10000):
        self.path, self.max_bytes, self.max_files = path, max_bytes, max_files
        path.parent.mkdir(parents=True, exist_ok=True)
        self.staging = StagingBudget(path.parent, max_bytes)
        self.timings = ProjectTimings()
        self.lock = threading.RLock()
        self.workers = threading.BoundedSemaphore(2)
        self.active = None
        self.generation = 0
        self.refreshing = set()
        self.errors = {}
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,root TEXT,generation INTEGER,updated REAL);
                CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,project TEXT,relative TEXT,revision TEXT,status TEXT);
                CREATE TABLE IF NOT EXISTS passages(id TEXT PRIMARY KEY,file TEXT,project TEXT,revision TEXT,locator_kind TEXT,locator TEXT,offset INTEGER,text TEXT);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id UNINDEXED,project UNINDEXED,text);
            """)

    @contextmanager
    def database(self):
        with self.lock:
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                page_size = db.execute("PRAGMA page_size").fetchone()[0]
                if type(self.max_bytes) is not int or self.max_bytes < page_size:
                    raise ToolError("invalid_index_capacity")
                pages = self.max_bytes // page_size
                if db.execute("PRAGMA page_count").fetchone()[0] > pages:
                    raise ToolError("index_capacity")
                # Each database() call opens a fresh connection. Enforce the real
                # allocated-page ceiling, including duplicated FTS content/indexes.
                actual = db.execute(f"PRAGMA max_page_count={pages}").fetchone()[0]
                if actual > pages:
                    raise ToolError("index_capacity")
                db.execute("PRAGMA secure_delete=ON")
                yield db
                db.commit()
            except sqlite3.Error as exc:
                db.rollback()
                if getattr(exc, "sqlite_errorcode", 0) & 255 == sqlite3.SQLITE_FULL:
                    raise ToolError("index_capacity") from None
                raise
            finally:
                db.close()

    def add_project(self, name, root):
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise ToolError("invalid_root")
        project = uuid.uuid4().hex
        with self.database() as db:
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?)", (project, name[:100], str(root), 0, 0)
            )
        return project

    def projects(self):
        with self.database() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects")]

    def _project(self, project):
        with self.database() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchone()
            if not row:
                raise ToolError("unknown_project")
            return dict(row)

    def activate(self, project):
        with self.lock:
            self._project(project)
            self.active = project
            self.generation += 1

    def snapshot(self):
        with self.lock:
            return {
                "projects": [self.status(p["id"]) for p in self.projects()],
                "active_project": self.active,
                "project_timings": self.timings.snapshot(),
            }

    def _active(self, project):
        if project != self.active:
            raise ToolError("inactive_project")
        return self._project(project)

    @staticmethod
    def _allowed(root, path, *, report_io_error=False):
        try:
            relative = path.relative_to(root)
            if any(p.lower() in EXCLUDED or p.lower().startswith(".env") for p in relative.parts):
                return False
            if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
                return False
            # Reject aliases even when target remains inside root: stable ownership and citations.
            current = path
            while current != root:
                if current.is_symlink() or current.is_junction():
                    return False
                current = current.parent
            return path.resolve(strict=True).is_relative_to(root)
        except OSError:
            if report_io_error:
                raise ToolError("source_unavailable") from None
            return False
        except ValueError:
            return False

    @staticmethod
    def _revision(path):
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ToolError("over_limit")
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()

    def _parse(self, path):
        with self.workers:
            try:
                status, output = capture_parser(
                    [sys.executable, "-m", "reachy_brain.knowledge.worker", str(path)],
                )
                if status != "ok":
                    return {"status": status, "passages": []}
                return json.loads(output)
            except (OSError, ValueError):
                return {"status": "unreadable", "passages": []}

    @timed("refresh")
    def reindex(self, project, valid=lambda: True, *, force=False):
        try:
            completed = self._reindex(project, valid, force=force)
        except (ToolError, OSError, sqlite3.Error) as exc:
            code = (
                exc.code
                if isinstance(exc, ToolError)
                and exc.code
                in {
                    "file_limit",
                    "index_capacity",
                    "index_staging_capacity",
                    "source_unavailable",
                    "canceled",
                }
                else "index_storage_error"
                if isinstance(exc, sqlite3.Error)
                else "index_failed"
            )
            with self.database() as db:
                if db.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
                    self.errors[project] = code
            raise
        else:
            if completed:
                with self.lock:
                    self.errors.pop(project, None)
            return completed

    def _reindex(self, project, valid=lambda: True, *, force=False):
        info = self._project(project)
        root = Path(info["root"])
        if not root.is_dir():
            raise ToolError("source_unavailable")
        with self.lock:
            if project in self.refreshing:
                return False
            self.refreshing.add(project)
        try:
            with self.staging.open() as updates:
                return self._scan(project, info, root, updates, valid, force)
        finally:
            with self.lock:
                self.refreshing.discard(project)

    def _scan(self, project, info, root, updates, valid, force):
        seen = set()
        with self.database() as db:
            known = {
                r["relative"]: dict(r)
                for r in db.execute("SELECT * FROM files WHERE project=?", (project,))
            }

        def unavailable(error):
            raise ToolError("source_unavailable") from None

        for directory, dirs, names in os.walk(root, followlinks=False, onerror=unavailable):
            dirs[:] = [
                d
                for d in dirs
                if d.lower() not in EXCLUDED
                and self._allowed(root, Path(directory) / d, report_io_error=True)
            ]
            for name in names:
                if not valid():
                    raise ToolError("canceled")
                path = Path(directory) / name
                relative = str(path.relative_to(root)).replace("\\", "/")
                if len(seen) >= self.max_files:
                    raise ToolError("file_limit")
                seen.add(relative)
                revision, result = "", {"status": "excluded", "passages": []}
                if self._allowed(root, path):
                    try:
                        revision = self._revision(path)
                        if (
                            not force
                            and relative in known
                            and known[relative]["revision"] == revision
                        ):
                            continue
                        result = self._parse(path)
                        if not self._allowed(root, path) or self._revision(path) != revision:
                            continue
                    except (OSError, ToolError):
                        result = {"status": "unavailable", "passages": []}
                if (
                    not force
                    and relative in known
                    and known[relative]["revision"] == revision
                    and known[relative]["status"] == result["status"]
                ):
                    continue
                updates.append((relative, revision, result))
        with self.database() as db:
            latest = db.execute("SELECT generation FROM projects WHERE id=?", (project,)).fetchone()
            if not latest or latest["generation"] != info["generation"] or not valid():
                raise ToolError("canceled")
            removed = set(known) - seen
            if not force and not removed and updates.size == 0 and info["updated"] > 0:
                return True  # Completed unchanged scan; no generation/timestamp write.
            for relative in removed:
                if relative in known:
                    self._delete_file(db, known[relative]["id"])
            for relative, revision, result in updates:
                if not valid():
                    raise ToolError("canceled")
                if relative in known:
                    self._delete_file(db, known[relative]["id"])
                file_id = hashlib.sha256((project + "/" + relative).encode()).hexdigest()[:32]
                db.execute(
                    "INSERT INTO files VALUES(?,?,?,?,?)",
                    (file_id, project, relative, revision, result["status"]),
                )
                for i, passage in enumerate(result["passages"]):
                    passage_id = hashlib.sha256((file_id + revision + str(i)).encode()).hexdigest()[
                        :32
                    ]
                    db.execute(
                        "INSERT INTO passages VALUES(?,?,?,?,?,?,?,?)",
                        (
                            passage_id,
                            file_id,
                            project,
                            revision,
                            passage["locator_kind"],
                            passage["locator"],
                            passage["offset"],
                            passage["text"],
                        ),
                    )
                    db.execute(
                        "INSERT INTO search VALUES(?,?,?)",
                        (passage_id, project, passage["text"]),
                    )
            if not valid():
                raise ToolError("canceled")
            db.execute(
                "UPDATE projects SET generation=generation+1,updated=? WHERE id=?",
                (time.time(), project),
            )
        return True

    @staticmethod
    def _delete_file(db, file_id):
        db.execute(
            "DELETE FROM search WHERE id IN (SELECT id FROM passages WHERE file=?)", (file_id,)
        )
        db.execute("DELETE FROM passages WHERE file=?", (file_id,))
        db.execute("DELETE FROM files WHERE id=?", (file_id,))

    def _verify(self, info, row):
        path = Path(info["root"]) / row["relative"]
        try:
            if (
                not self._allowed(Path(info["root"]), path)
                or self._revision(path) != row["revision"]
            ):
                raise ToolError("stale")
        except OSError:
            raise ToolError("stale") from None
        return {
            "id": row["id"],
            "project_id": info["id"],
            "project": info["name"],
            "path": row["relative"],
            "revision": row["revision"],
            "locator_kind": row["locator_kind"],
            "locator": row["locator"],
            "text": row["text"][:2000],
        }

    @timed("search")
    def search(self, project, query, limit=8):
        generation = self.generation
        info = self._active(project)
        words = re.findall(r"\w+", query[:500], flags=re.UNICODE)[:20]
        if not words:
            return []
        expression = " OR ".join('"' + w + '"' for w in words)
        with self.database() as db:
            rows = db.execute(
                "SELECT p.*,f.relative FROM search s JOIN passages p ON p.id=s.id JOIN files f ON f.id=p.file WHERE search MATCH ? AND p.project=? ORDER BY bm25(search) LIMIT 32",
                (expression, project),
            ).fetchall()
        hits = []
        for row in rows:
            try:
                hits.append(self._verify(info, row))
            except ToolError:
                continue
            if len(hits) >= min(8, max(1, limit)):
                break
        self._active(project)
        if generation != self.generation:
            raise ToolError("stale_project")
        return hits

    @timed("read")
    def read(self, project, ids):
        generation = self.generation
        info = self._active(project)
        if len(ids) > 4:
            raise ToolError("passage_limit")
        rows = []
        with self.database() as db:
            for passage_id in ids:
                row = db.execute(
                    "SELECT p.*,f.relative FROM passages p JOIN files f ON f.id=p.file WHERE p.id=? AND p.project=?",
                    (passage_id, project),
                ).fetchone()
                if not row:
                    raise ToolError("stale")
                rows.append(row)
        # File hashing must not hold the SQLite lock and block selection/control work.
        output = [self._verify(info, row) for row in rows]
        self._active(project)
        if generation != self.generation:
            raise ToolError("stale_project")
        return output

    def status(self, project):
        info = self._project(project)
        with self.database() as db:
            info["files"] = [
                dict(r)
                for r in db.execute(
                    "SELECT relative,status FROM files WHERE project=? ORDER BY relative LIMIT 1000",
                    (project,),
                )
            ]
            info["coverage"] = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status,COUNT(*) AS count FROM files WHERE project=? GROUP BY status",
                    (project,),
                )
            }
            info["files_total"] = sum(info["coverage"].values())
            info["files_truncated"] = info["files_total"] > len(info["files"])
            info["error"] = self.errors.get(project)
        info["indexing"] = project in self.refreshing
        return info

    def remove(self, project):
        with self.lock:
            with self.database() as db:
                db.execute("DELETE FROM search WHERE project=?", (project,))
                db.execute("DELETE FROM passages WHERE project=?", (project,))
                db.execute("DELETE FROM files WHERE project=?", (project,))
                db.execute("DELETE FROM projects WHERE id=?", (project,))
            self.errors.pop(project, None)
            if self.active == project:
                self.active = None
            self.generation += 1
        # Secure-delete content; compaction removes unused pages without touching originals.
        try:
            self.compact()
        except ToolError:
            raise ToolError("project_removed_storage_cleanup_incomplete") from None

    def compact(self):
        try:
            with self.database() as db:
                db.execute("VACUUM")
        except (OSError, sqlite3.Error, ToolError):
            raise ToolError("index_storage_cleanup_failed") from None
