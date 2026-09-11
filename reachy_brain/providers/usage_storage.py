"""Atomic bounded accounting checkpoints, written off the audio/control event loop."""

import asyncio
import contextlib
import os
import tempfile
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    estimated_usd: float = Field(default=0, ge=0)
    unknown_charges: int = Field(default=0, ge=0)
    identities: list[
        tuple[
            Annotated[str, StringConstraints(min_length=1, max_length=64)],
            Annotated[str, StringConstraints(min_length=1, max_length=256)],
        ]
    ] = Field(default_factory=list, max_length=10000)


class UsageStorage:
    maximum_bytes = 4 * 1024 * 1024

    def __init__(self, gate, path):
        self.gate, self.path = gate, path
        self.revision = self.persisted = 0
        self.error = None
        self.closing = False
        self.wake = asyncio.Event()
        self.worker = None
        self.starting = False
        self.lock_file = None
        self.cleanup = None
        self.opening = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        try:
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("usage_storage_lock_unavailable") from None
        self.lock_file = lock

    def release(self):
        lock, self.lock_file = self.lock_file, None
        if lock:
            # Closing releases the OS lock, including after process termination.
            # Keep the sidecar: deleting it could create two independently locked files.
            lock.close()

    def open_owned(self):
        self.acquire()
        try:
            return self.load()
        except BaseException:
            self.release()
            raise

    def load(self):
        if not self.path.exists():
            return Checkpoint()
        with self.path.open("rb") as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise ValueError("usage_storage_limit")
        value = Checkpoint.model_validate_json(raw)
        if len(set(value.identities)) != len(value.identities):
            raise ValueError("usage_storage_duplicate_identity")
        return value

    async def start(self):
        if self.starting or self.closing or self.worker or self.gate.seen_usage or self.gate.usage:
            raise RuntimeError("usage_storage_already_active")
        self.starting = True
        self.opening = asyncio.create_task(asyncio.to_thread(self.open_owned))
        try:
            saved = await asyncio.shield(self.opening)
        except asyncio.CancelledError:
            # A canceled await does not stop its I/O thread. Join acquisition
            # before releasing so it cannot acquire a forgotten lease later.
            await self.close()
            raise
        except Exception as exc:
            self.error = (
                "usage_storage_lock_unavailable"
                if isinstance(exc, RuntimeError) and str(exc) == "usage_storage_lock_unavailable"
                else "usage_storage_invalid"
            )
            raise RuntimeError(self.error) from None
        finally:
            self.starting = False
        if self.closing:
            await self.close()
            raise RuntimeError("usage_storage_closed")
        self.gate.estimated_usd = saved.estimated_usd
        self.gate.unknown_charges = saved.unknown_charges
        self.gate.seen_usage = set(saved.identities)
        self.revision = self.persisted = saved.revision
        self.gate.persistence = self
        self.worker = asyncio.create_task(self.run())

    def changed(self):
        self.revision += 1
        if self.closing:
            self.error = "usage_after_shutdown"
        self.wake.set()

    def status(self):
        return {
            "status": self.error or ("pending" if self.revision != self.persisted else "saved"),
            "revision": self.revision,
            "persisted_revision": self.persisted,
            "identity_count": len(self.gate.seen_usage),
            "identity_capacity": 10000,
        }

    def write(self, value):
        raw = value.model_dump_json().encode("utf-8")
        if len(raw) > self.maximum_bytes:
            raise ValueError("usage_storage_limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".usage-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def run(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            if self.revision != self.persisted:
                try:
                    snapshot = Checkpoint(
                        revision=self.revision,
                        estimated_usd=self.gate.estimated_usd,
                        unknown_charges=self.gate.unknown_charges,
                        identities=list(self.gate.seen_usage),
                    )
                    await asyncio.to_thread(self.write, snapshot)
                    self.persisted = snapshot.revision
                except Exception:
                    self.error = "usage_persistence_failed"
                    return
            if self.closing and self.revision == self.persisted:
                return

    async def close(self):
        self.closing = True
        self.wake.set()
        if self.cleanup is None:
            self.cleanup = asyncio.create_task(self.finish())
        await asyncio.shield(self.cleanup)

    async def finish(self):
        try:
            if self.opening:
                with contextlib.suppress(Exception):
                    await self.opening
            if self.worker:
                await self.worker
        finally:
            await asyncio.to_thread(self.release)
        if self.error:
            raise RuntimeError(self.error)
