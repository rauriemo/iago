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
