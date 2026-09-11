"""Actual SQLite page limits with synthetic operation identities and references."""

import pytest

from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import ToolError


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-JOURNAL-IDENTITY-BOUNDS")
@pytest.mark.parametrize(
    "field,value",
    [
        ("identity", "x" * 129),
        ("tool", "x" * 257),
        ("generation", True),
        ("generation", 2**63),
        ("identity", ""),
        ("op", 12),
    ],
)
def test_begin_enforces_same_identity_bounds_as_prepare(tmp_path, field, value):
    store = OperationStore(tmp_path / "operations.sqlite")
    args = dict(op="operation", identity="a" * 64, tool="fake__local__write", generation=0)
    args[field] = value
    try:
        with pytest.raises(ToolError, match="invalid_operation_identity"):
            store.begin(**args)
        assert store.recent() == []
    finally:
        store.close()


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-JOURNAL-UPDATE-PAGE-BOUND")
def test_late_outcomes_remain_bounded_and_recoverable(tmp_path):
    path = tmp_path / "operations.sqlite"
    limit = 65536
    store = OperationStore(path, max_bytes=limit)
    admitted = []
    capacity_failures = 0
    try:
        page_size = store.db.execute("PRAGMA page_size").fetchone()[0]
        assert store.db.execute("PRAGMA max_page_count").fetchone()[0] * page_size <= limit
        for index in range(1000):
            op = f"{index}:" + "a" * 110
            try:
                store.begin(op, "b" * 128, "c" * 256, 0)
            except ToolError as exc:
                assert exc.code == "journal_capacity"
                break
            admitted.append(op)
        assert admitted and len(admitted) < 1000
        for op in admitted:
            try:
                store.finish(op, "succeeded", "界" * 128)
            except ToolError as exc:
                assert exc.code == "journal_capacity"
                assert store.get(op)["status"] == "dispatched"
                capacity_failures += 1
            assert path.stat().st_size <= limit
        assert capacity_failures > 0
        before = {op: store.get(op)["status"] for op in admitted}
    finally:
        store.close()
    reopened = OperationStore(path, max_bytes=limit)
    try:
        for op, status in before.items():
            assert reopened.get(op)["status"] == ("uncertain" if status == "dispatched" else status)
            assert not reopened.begin(op, "b" * 128, "c" * 256, 0)
        assert path.stat().st_size <= limit
    finally:
        reopened.close()


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-JOURNAL-LOWERED-CAPACITY")
def test_lower_limit_does_not_modify_or_hold_existing_database(tmp_path):
    path = tmp_path / "operations.sqlite"
    store = OperationStore(path)
    for index in range(100):
        store.begin(str(index), "a" * 128, "b" * 256, 0)
    store.close()
    before = path.read_bytes()
    assert len(before) > 12288
    with pytest.raises(ToolError, match="journal_capacity"):
        OperationStore(path, max_bytes=12288)
    assert path.read_bytes() == before
    # On this Windows host the rename also checks that failed startup released its handle.
    moved = path.with_name("retained.sqlite")
    path.rename(moved)
    reopened = OperationStore(moved)
    try:
        assert reopened.get("0")["status"] == "uncertain"
    finally:
        reopened.close()
