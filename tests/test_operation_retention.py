"""Real private SQLite limits with synthetic IDs and an explicit test clock."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import ToolError
from reachy_brain.web.app import create_app


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-JOURNAL-RETENTION-CAPACITY")
def test_terminal_expiry_reclaims_capacity_without_evicting_uncertainty(tmp_path):
    clock = [100.0]
    journal = OperationStore(
        tmp_path / "operations.sqlite", max_bytes=65536, retention_days=1, clock=lambda: clock[0]
    )
    try:
        journal.begin("unresolved", "a" * 64, "fake__local__write", 0)
        journal.finish("unresolved", "uncertain", "opaque-fake-reference")
        terminal = []
        for index in range(1000):
            op = f"terminal-{index:04d}" + "x" * 100
            try:
                journal.begin(op, "b" * 64, "fake__local__write", 0)
            except ToolError as error:
                assert error.code == "journal_capacity"
                break
            journal.finish(op, "succeeded", "r" * 128)
            terminal.append(op)
        else:
            pytest.fail("Journal did not enforce capacity")
        assert terminal
        assert journal.path.stat().st_size <= journal.max_bytes
        assert not journal.storage_status()["accepting_new_writes"]
        clock[0] += 86400
        assert journal.cleanup() == 0  # Boundary records are retained.
        clock[0] += 1
        assert journal.cleanup() == len(terminal)
        assert journal.get("unresolved")["provider_ref"] == "opaque-fake-reference"
        assert journal.storage_status()["unresolved"] == 1
        assert journal.storage_status()["accepting_new_writes"]
        assert journal.prepare("new", "c" * 64, "fake__local__write", 0)
        assert not journal.begin("unresolved", "a" * 64, "fake__local__write", 0)
        for limit in [-1, 0, 51, True]:
            with pytest.raises(ToolError, match="invalid_operation_limit"):
                journal.recent(limit)
    finally:
        journal.close()


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-JOURNAL-STARTUP-CLEANUP")
def test_app_applies_configured_retention_and_exposes_storage(tmp_path):
    path = tmp_path / "operations.sqlite"
    journal = OperationStore(path, clock=lambda: 1.0)
    journal.begin("old-success", "a" * 64, "fake__local__write", 0)
    journal.finish("old-success", "succeeded")
    journal.begin("old-uncertain", "b" * 64, "fake__local__write", 0)
    journal.finish("old-uncertain", "uncertain")
    journal.close()
    settings = Settings(
        _env_file=None, data_dir=tmp_path, operation_retention_days=2, operation_max_mib=1
    )
    app = create_app(settings, token="test")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test"
        result = client.get("/api/operations").json()
        assert [row["id"] for row in result["operations"]] == ["old-uncertain"]
        assert result["storage"]["max_bytes"] == 1024 * 1024
        assert result["storage"]["retention_days"] == 2
        assert result["storage"]["unresolved"] == 1


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-JOURNAL-PERIODIC-CLEANUP")
async def test_running_app_cleans_expired_terminal_records(tmp_path):
    # Accelerated timer is explicitly test-only; normal validated minimum is 60 s.
    settings = Settings(_env_file=None, data_dir=tmp_path).model_copy(
        update={"operation_cleanup_seconds": 0.02}
    )
    app = create_app(settings, token="test")
    async with app.router.lifespan_context(app):
        journal = app.state.executor.operations
        cleaned = asyncio.Event()
        loop = asyncio.get_running_loop()
        original_cleanup = journal.cleanup

        def observe_cleanup():
            removed = original_cleanup()
            if removed:
                loop.call_soon_threadsafe(cleaned.set)
            return removed

        journal.cleanup = observe_cleanup
        real_clock = journal.clock
        journal.clock = lambda: 1.0
        await asyncio.to_thread(journal.begin, "expired", "a" * 64, "fake__local__write", 0)
        await asyncio.to_thread(journal.finish, "expired", "succeeded")
        journal.clock = real_clock
        await asyncio.wait_for(cleaned.wait(), timeout=2)
        assert await asyncio.to_thread(journal.get, "expired") is None
