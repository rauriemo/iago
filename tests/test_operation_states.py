"""Durable pre-dispatch state contracts; executor wiring is tested separately."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import ToolError


@pytest.mark.features("E1")
@pytest.mark.scenario("E1-PREDISPATCH-RESTART-STATES")
def test_restart_cancels_undispatched_and_preserves_uncertain_and_terminal(tmp_path):
    path = tmp_path / "operations.sqlite"
    journal = OperationStore(path)
    states = [
        "proposed",
        "awaiting-confirmation",
        "queued",
        "dispatched",
        "uncertain",
        "reconciling",
        "succeeded",
    ]
    try:
        for status in states:
            assert journal.prepare(status, "b" * 64, "calendar__test__create", 0)
            if status == "proposed":
                continue
            assert journal.transition(status, "proposed", "awaiting-confirmation")
            if status == "awaiting-confirmation":
                continue
            assert journal.transition(status, "awaiting-confirmation", "queued", "a" * 64)
            if status == "queued":
                continue
            assert journal.transition(status, "queued", "dispatched")
            if status in {"uncertain", "reconciling"}:
                journal.finish(status, "uncertain")
            if status == "reconciling":
                assert journal.claim_reconciliation(status)
            if status == "succeeded":
                journal.finish(status, "succeeded", "synthetic-ref")
    finally:
        journal.close()
    reopened = OperationStore(path)
    try:
        for original in states:
            expected = (
                "canceled-before-dispatch"
                if original in states[:3]
                else "succeeded"
                if original == "succeeded"
                else "uncertain"
            )
            assert reopened.get(original)["status"] == expected
        assert reopened.get("succeeded")["provider_ref"] == "synthetic-ref"
        assert reopened.get("queued")["authorization_ref"] == "a" * 64
        assert not reopened.transition("queued", "queued", "dispatched")
    finally:
        reopened.close()


@pytest.mark.features("E1", "C2")
@pytest.mark.scenario("E1-QUEUED-CANCEL-DISPATCH-CLAIM")
def test_queue_cancel_and_dispatch_are_mutually_exclusive(tmp_path):
    journal = OperationStore(tmp_path / "operations.sqlite")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for number in range(20):
                op = f"op-{number}"
                assert journal.prepare(op, "b" * 64, "calendar__test__create", 0)
                assert not journal.prepare(op, "b" * 64, "calendar__test__create", 0)
                with pytest.raises(ToolError, match="operation_conflict"):
                    journal.prepare(op, "c" * 64, "calendar__test__create", 0)
                with pytest.raises(ToolError, match="invalid_authorization_reference"):
                    journal.transition(op, "proposed", "queued")
                assert journal.transition(op, "proposed", "queued", "a" * 64)
                barrier = threading.Barrier(2)

                def race(cancel, op=op, barrier=barrier):
                    barrier.wait(timeout=2)
                    return (
                        journal.cancel_undispatched(op)
                        if cancel
                        else journal.transition(op, "queued", "dispatched")
                    )

                cancel = pool.submit(race, True)
                dispatch = pool.submit(race, False)
                outcomes = [cancel.result(timeout=3), dispatch.result(timeout=3)]
                assert sorted(outcomes) == [False, True]
                assert journal.get(op)["status"] == (
                    "canceled-before-dispatch" if outcomes[0] else "dispatched"
                )
                assert not journal.cancel_undispatched(op)
    finally:
        journal.close()
