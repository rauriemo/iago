"""Actual OS/process lock ownership; no provider calls or physical claims."""

import asyncio
import os
import subprocess
import sys
import threading

import pytest

from reachy_brain.config import Settings
from reachy_brain.providers.live import ProviderError, ProviderGate
from reachy_brain.providers.usage_storage import UsageStorage


@pytest.mark.features("D6")
@pytest.mark.scenario("USAGE-ABRUPT-EXIT-RECOVERY")
@pytest.mark.parametrize("state", ["active", "limited", "both"])
async def test_saved_admission_and_limit_survive_abrupt_child_exit(tmp_path, state):
    script = """
import asyncio, os, sys
from pathlib import Path
from reachy_brain.config import Settings
from reachy_brain.providers.live import ProviderGate
from reachy_brain.providers.usage_storage import UsageStorage
async def main():
    gate = ProviderGate(Settings(_env_file=None, iago_development_budget="unlimited"))
    store = UsageStorage(gate, Path(sys.argv[1]))
    await store.start()
    if sys.argv[2] in ("active", "both"):
        gate.begin_active("astra", "gpt-6-astra", {"private": "never-save-this"})
        await gate.checkpoint("openai")
    if sys.argv[2] in ("limited", "both"):
        gate.limit("openai")
        await store.flush()
    # Deliberately bypass finally blocks, store.close and normal Python shutdown.
    os._exit(17)
asyncio.run(main())
"""
    path = tmp_path / "usage.json"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(path),
        state,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env={
            k: os.environ[k]
            for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE")
            if k in os.environ
        },
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        _, error = await asyncio.wait_for(process.communicate(), 20)
        assert process.returncode == 17, error.decode(errors="replace")[-1000:]
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert "never-save-this" not in path.read_text(encoding="utf-8")
    for _ in range(2):
        gate = ProviderGate(Settings(_env_file=None, iago_development_budget="unlimited"))
        successor = UsageStorage(gate, path)
        await successor.start()
        try:
            assert gate.unknown_charges == int(state in {"active", "both"})
            assert gate.estimated_usd == 0 and not gate.active and not gate.usage
            if state in {"limited", "both"}:
                with pytest.raises(ProviderError, match="plan_limit"):
                    gate.require("openai")
            else:
                gate.require("openai")
            gate.require("elevenlabs")
        finally:
            await successor.close()


@pytest.mark.features("D6")
@pytest.mark.scenario("USAGE-PROCESS-OWNERSHIP")
@pytest.mark.parametrize("exit_mode", ["close", "terminate"])
async def test_second_process_cannot_overwrite_and_exit_releases_lock(tmp_path, exit_mode):
    script = """
import asyncio, sys
from pathlib import Path
from reachy_brain.config import Settings
from reachy_brain.providers.live import ProviderGate
from reachy_brain.providers.usage_storage import UsageStorage
async def main():
    gate = ProviderGate(Settings(_env_file=None))
    store = UsageStorage(gate, Path(sys.argv[1]))
    await store.start()
    gate.record("astra", "child-request", {})
    while store.persisted != store.revision:
        await asyncio.sleep(0.01)
    print("READY", flush=True)
    await asyncio.to_thread(sys.stdin.readline)
    await store.close()
asyncio.run(main())
"""
    path = tmp_path / "usage.json"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={
            k: os.environ[k]
            for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE")
            if k in os.environ
        },
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        assert (await asyncio.wait_for(process.stdout.readline(), 15)).rstrip(b"\r\n") == b"READY"
        original = await asyncio.to_thread(path.read_bytes)
        contender = UsageStorage(ProviderGate(Settings(_env_file=None)), path)
        with pytest.raises(RuntimeError, match="usage_storage_lock_unavailable"):
            await contender.start()
        assert await asyncio.to_thread(path.read_bytes) == original
        if exit_mode == "close":
            process.stdin.write(b"close\n")
            await process.stdin.drain()
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)
        gate = ProviderGate(Settings(_env_file=None))
        successor = UsageStorage(gate, path)
        await successor.start()
        try:
            assert gate.unknown_charges == 1
            gate.record("astra", "next-request", {})
        finally:
            await successor.close()
        saved = await asyncio.to_thread(successor.load)
        assert saved.unknown_charges == 2
        assert set(saved.identities) == {("astra", "child-request"), ("astra", "next-request")}
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.features("D6")
@pytest.mark.scenario("USAGE-CANCELED-ACQUISITION")
async def test_cancel_start_joins_thread_and_releases_acquired_ownership(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    storage = UsageStorage(ProviderGate(Settings(_env_file=None)), path)
    entered, release = threading.Event(), threading.Event()
    original = storage.load

    def blocked():
        entered.set()
        assert release.wait(5), "Synthetic read barrier not released"
        return original()

    monkeypatch.setattr(storage, "load", blocked)
    opening = asyncio.create_task(storage.start())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        opening.cancel()
        await asyncio.sleep(0)
        opening.cancel()  # Cleanup owns the acquisition even if the caller is canceled again.
        contender = UsageStorage(ProviderGate(Settings(_env_file=None)), path)
        with pytest.raises(RuntimeError, match="usage_storage_lock_unavailable"):
            await contender.start()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await opening
        await storage.close()
    successor = UsageStorage(ProviderGate(Settings(_env_file=None)), path)
    await successor.start()
    await successor.close()
    assert storage.lock_file is None


@pytest.mark.features("C2", "D6")
@pytest.mark.scenario("USAGE-CANCELED-SHUTDOWN-OWNERSHIP")
async def test_canceled_shutdown_keeps_lock_until_write_finishes(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    gate = ProviderGate(Settings(_env_file=None))
    storage = UsageStorage(gate, path)
    entered, release = threading.Event(), threading.Event()
    original = storage.write

    def blocked(value):
        entered.set()
        assert release.wait(5), "Synthetic write barrier not released"
        original(value)

    monkeypatch.setattr(storage, "write", blocked)
    await storage.start()
    gate.record("astra", "pending", {})
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(storage.close())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        contender = UsageStorage(ProviderGate(Settings(_env_file=None)), path)
        with pytest.raises(RuntimeError, match="usage_storage_lock_unavailable"):
            await contender.start()
    finally:
        release.set()
        await storage.close()
    successor = UsageStorage(ProviderGate(Settings(_env_file=None)), path)
    await successor.start()
    try:
        assert successor.gate.unknown_charges == 1
    finally:
        await successor.close()
