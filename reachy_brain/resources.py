"""Bounded numeric backend resource samples; never collect process commands or environment."""

import math
import threading
import time
from collections import deque

import psutil


class ResourceMonitor:
    def __init__(self, *, capacity=1080, process=None, clock=time.time, monotonic=time.monotonic):
        self.process = process or psutil.Process()
        self.clock, self.monotonic = clock, monotonic
        self.samples = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.previous = None
        self.total = 0

    def sample(self):
        at, tick = self.clock(), self.monotonic()
        row = {"at": at, "status": "available"}
        try:
            with self.process.oneshot():
                cpu = self.process.cpu_times()
                seconds = cpu.user + cpu.system
                row["backend_rss_bytes"] = self.process.memory_info().rss
                row["backend_threads"] = self.process.num_threads()
            row["backend_cpu_seconds"] = seconds
            row["backend_cpu_percent"] = None
            if self.previous and tick > self.previous[0]:
                row["backend_cpu_percent"] = (
                    max(0, seconds - self.previous[1]) / (tick - self.previous[0]) * 100
                )
            self.previous = (tick, seconds)
            children = self.process.children(recursive=True)
            row["child_count"] = len(children)
            row["children_rss_bytes"] = 0
            row["unavailable_children"] = 0
            for child in children:
                try:
                    row["children_rss_bytes"] += child.memory_info().rss
                except (psutil.Error, OSError):
                    row["unavailable_children"] += 1
        except (psutil.Error, OSError, NotImplementedError):
            row = {"at": at, "status": "unavailable"}
            self.previous = None
        temperatures = []
        try:
            read = getattr(psutil, "sensors_temperatures", None)
            for group in (read() if read else {}).values():
                for sensor in group:
                    if sensor.current is not None and math.isfinite(sensor.current):
                        temperatures.append(float(sensor.current))
        except (psutil.Error, OSError, NotImplementedError):
            pass
        row["temperature_celsius"] = temperatures[:16]
        row["temperature_available"] = bool(temperatures)
        with self.lock:
            self.samples.append(row)
            self.total += 1

    def snapshot(self, *, history=False):
        with self.lock:
            selected = self.samples if history else ([self.samples[-1]] if self.samples else [])
            rows = [
                {**row, "temperature_celsius": list(row["temperature_celsius"])} for row in selected
            ]
            result = {
                "total_samples": self.total,
                "retained_samples": len(self.samples),
                "capacity": self.samples.maxlen,
                "latest": rows[-1] if rows else None,
                "age_seconds": max(0, self.clock() - rows[-1]["at"]) if rows else None,
                "stale": not rows or self.clock() - rows[-1]["at"] > 15,
            }
            if history:
                result["samples"] = rows
            return result
