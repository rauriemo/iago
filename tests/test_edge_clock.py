import pytest

from reachy_brain.robot.client import ClockEstimate, EdgeClient


@pytest.mark.features("D2", "D3", "V5")
@pytest.mark.scenario("EDGE-CLOCK-UNCERTAINTY")
def test_clock_mapping_retains_uncertainty_and_expires():
    clock = ClockEstimate()
    clock.observe(10, 10.2, 15.1)
    result = clock.local(16, now=11)
    assert result["time"] == pytest.approx(11)
    assert result["uncertainty"] >= 0.1
    assert not result["stale"]
    assert clock.local(30, now=26)["stale"]
    clock.observe(27, 27.02, 32.01)
    assert clock.uncertainty == pytest.approx(0.01)


@pytest.mark.features("D2", "D3", "D5")
@pytest.mark.scenario("EDGE-TRANSPORT-SECURITY")
@pytest.mark.parametrize(
    "url",
    ["http://robot.local:8877", "https://token@robot.local", "https://robot.local/?key=secret"],
)
def test_edge_rejects_insecure_or_embedded_credentials(url):
    with pytest.raises(ValueError):
        EdgeClient(url, "synthetic-token")


@pytest.mark.features("D2", "D3", "V5")
@pytest.mark.scenario("EDGE-CLOCK-DISCONTINUITY")
@pytest.mark.parametrize("jump", [-30, 30])
def test_changed_remote_clock_replaces_incompatible_faster_sample(jump):
    clock = ClockEstimate()
    clock.observe(100, 100.02, 105.01)
    clock.observe(101, 101.1, 106.05 + jump)
    result = clock.local(107 + jump, now=102)
    assert result["time"] == pytest.approx(102)
    assert result["uncertainty"] >= 0.05
    assert not result["stale"]


@pytest.mark.features("D2", "D3", "V5")
@pytest.mark.scenario("EDGE-CLOCK-LOCAL-ROLLBACK")
def test_local_clock_rollback_retires_mapping_until_new_observation():
    clock = ClockEstimate()
    clock.observe(100, 100.02, 105.01)
    assert clock.local(106, now=90)["stale"]
    assert clock.local(106, now=101)["stale"]  # Catch-up cannot revive the retired estimate.
    clock.observe(90, 90.1, 106.05)
    result = clock.local(107, now=91)
    assert result["time"] == pytest.approx(91)
    assert not result["stale"]


@pytest.mark.features("D2", "D3", "V5")
@pytest.mark.scenario("EDGE-CLOCK-INVALID-MAPPING")
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True, "100"])
@pytest.mark.parametrize("field", ["remote", "now"])
def test_invalid_mapping_coordinates_are_rejected(invalid, field):
    clock = ClockEstimate()
    clock.observe(100, 100.02, 105.01)
    values = {"remote": 106, "now": 101, field: invalid}
    with pytest.raises(ValueError, match="invalid_clock_mapping"):
        clock.local(**values)


@pytest.mark.features("D2", "D3", "V5")
@pytest.mark.scenario("EDGE-CLOCK-COMPATIBLE-SAMPLES")
def test_compatible_slower_sample_preserves_better_estimate():
    clock = ClockEstimate()
    assert clock.local(0, now=0)["stale"]
    clock.observe(100, 100.02, 105.01)
    clock.observe(101, 101.1, 106.06)
    assert clock.offset == pytest.approx(5)
    assert clock.uncertainty == pytest.approx(0.01)
    assert clock.updated == pytest.approx(100.02)


@pytest.mark.features("D2", "D3", "V5")
@pytest.mark.scenario("EDGE-CLOCK-INVALID-OBSERVATION")
@pytest.mark.parametrize("sample", [(True, 2, 3), (1, "2", 3), (1, 2, float("nan")), (3, 2, 4)])
def test_invalid_observation_preserves_previous_estimate(sample):
    clock = ClockEstimate()
    clock.observe(100, 100.02, 105.01)
    before = (clock.offset, clock.uncertainty, clock.updated)
    with pytest.raises(ValueError, match="invalid_clock_sample"):
        clock.observe(*sample)
    assert (clock.offset, clock.uncertainty, clock.updated) == before
