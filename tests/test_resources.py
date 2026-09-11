"""Numeric resource sampling with deterministic limits and actual local HTTP access."""

from contextlib import nullcontext
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.resources import ResourceMonitor
from reachy_brain.web.app import create_app


@pytest.mark.features("D5", "D6")
@pytest.mark.scenario("RESOURCE-SAMPLE-BOUNDS")
def test_resource_window_cpu_and_snapshot_ownership(monkeypatch):
    tick = [0]

    class Process:
        def oneshot(self):
            return nullcontext()

        def cpu_times(self):
            return SimpleNamespace(user=tick[0] / 2, system=0)

        def memory_info(self):
            return SimpleNamespace(rss=4096)

        def num_threads(self):
            return 3

        def children(self, recursive):
            return []

    monkeypatch.setattr("psutil.sensors_temperatures", lambda: {}, raising=False)
    monitor = ResourceMonitor(
        capacity=2, process=Process(), clock=lambda: tick[0], monotonic=lambda: tick[0]
    )
    for at in (0, 5, 10):
        tick[0] = at
        monitor.sample()
    result = monitor.snapshot(history=True)
    assert result["total_samples"] == 3 and result["retained_samples"] == 2
    assert [row["at"] for row in result["samples"]] == [5, 10]
    assert result["latest"]["backend_cpu_percent"] == 50
    assert not result["latest"]["temperature_available"]
    result["latest"]["temperature_celsius"].append(99)
    assert monitor.snapshot()["latest"]["temperature_celsius"] == []
    assert "samples" not in monitor.snapshot()


@pytest.mark.features("D5")
@pytest.mark.scenario("RESOURCE-HTTP-REAL-PROCESS")
def test_resource_endpoint_is_private_and_returns_real_numeric_samples(tmp_path):
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    ) as client:
        assert client.get("/api/resources").status_code == 401
        client.headers["Authorization"] = "Bearer test"
        # Initial worker submission may not have completed before the first HTTP request.
        import time

        deadline = time.monotonic() + 2
        while True:
            result = client.get("/api/resources").json()
            if result["latest"]:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert result["latest"]["status"] == "available"
        assert result["latest"]["backend_rss_bytes"] > 0
        assert result["latest"]["backend_threads"] > 0
        assert result["capacity"] == 1080
        assert set(result["latest"]) == {
            "at",
            "status",
            "backend_rss_bytes",
            "backend_threads",
            "backend_cpu_seconds",
            "backend_cpu_percent",
            "child_count",
            "children_rss_bytes",
            "unavailable_children",
            "temperature_celsius",
            "temperature_available",
        }


@pytest.mark.features("D5", "D6")
@pytest.mark.scenario("RESOURCE-FAILURE-RECOVERY-FRESHNESS")
def test_unavailable_sample_recovers_without_false_cpu_baseline(monkeypatch):
    tick = [0]

    class DisappearingChild:
        def memory_info(self):
            raise psutil.NoSuchProcess(123)

    class Process:
        def oneshot(self):
            return nullcontext()

        def cpu_times(self):
            if tick[0] == 5:
                raise NotImplementedError("Synthetic unavailable counter")
            return SimpleNamespace(user=tick[0], system=0)

        def memory_info(self):
            return SimpleNamespace(rss=4096)

        def num_threads(self):
            return 3

        def children(self, recursive):
            return [DisappearingChild()]

    def unavailable_temperature():
        raise OSError("Synthetic sensor failure")

    monkeypatch.setattr(psutil, "sensors_temperatures", unavailable_temperature, raising=False)
    monitor = ResourceMonitor(process=Process(), clock=lambda: tick[0], monotonic=lambda: tick[0])
    assert monitor.snapshot()["stale"]
    for at in (0, 5, 10):
        tick[0] = at
        monitor.sample()
    result = monitor.snapshot(history=True)
    assert [sample["status"] for sample in result["samples"]] == [
        "available",
        "unavailable",
        "available",
    ]
    assert "backend_cpu_percent" not in result["samples"][1]
    assert result["latest"]["backend_cpu_percent"] is None
    assert result["latest"]["child_count"] == result["latest"]["unavailable_children"] == 1
    assert not result["latest"]["temperature_available"]
    assert result["age_seconds"] == 0 and not result["stale"]
    tick[0] = 30
    assert monitor.snapshot()["age_seconds"] == 20 and monitor.snapshot()["stale"]
