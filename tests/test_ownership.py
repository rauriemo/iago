"""Deterministic fake sink tests, not physical speaker evidence."""

import pytest

from reachy_brain.core.ownership import HeardLedger, PlaybackGuard


@pytest.mark.features("C2", "C8", "D2", "D5")
@pytest.mark.scenario("CANCEL-OWNERSHIP")
def test_stop_latches_against_inflight_authorization():
    guard = PlaybackGuard(lease_seconds=1)
    guard.connect("session", "connection", now=0)
    assert guard.authorize(1, acknowledged_stop=0, now=0)
    assert guard.accept(1, 0, now=0.1)
    guard.stop()
    assert not guard.authorize(2, acknowledged_stop=0, now=0.2)
    assert not guard.accept(1, 1, now=0.2)
    assert guard.authorize(2, acknowledged_stop=1, now=0.2)
    assert guard.accept(2, 0, now=0.3)
    assert not guard.accept(2, 0, now=0.4)
    assert not guard.accept(2, 1, now=1.1)


@pytest.mark.features("C2", "C8")
@pytest.mark.scenario("CANCEL-HEARD")
def test_heard_ledger_excludes_unheard_segments():
    ledger = HeardLedger()
    ledger.add(1, "one", "First item.")
    ledger.add(1, "two", "Secret second item.")
    ledger.acknowledge(1, "one")
    ledger.interrupt(1)
    assert ledger.heard(1) == "First item."
    ledger.acknowledge(1, "two")
    assert ledger.heard(1) == "First item."
