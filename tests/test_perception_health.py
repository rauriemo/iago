"""Worker liveness reporting; real short-lived processes, no camera inference claim."""

import multiprocessing
import os
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from reachy_brain.vision.worker import PerceptionWorker


def wrapper(process):
    worker = PerceptionWorker.__new__(PerceptionWorker)
    worker.process = process
    worker.stop_event = threading.Event()
    worker.outgoing = queue.Queue()
    worker.exit_reported = False
    worker.count = worker.dropped = 0
    worker.started = time.monotonic()
    worker.offered = 0
    worker.last_offered = worker.last_result = None
    worker.incoming = queue.Queue(maxsize=1)
    return worker


@pytest.mark.features("P1", "D5", "D6")
@pytest.mark.scenario("PERCEPTION-UNEXPECTED-PROCESS-EXIT")
@pytest.mark.parametrize("code", [0, 7])
def test_unexpected_process_exit_without_queue_message_is_reported_once(code):
    process = multiprocessing.get_context("spawn").Process(target=os._exit, args=(code,))
    process.start()
    try:
        process.join(5)
        assert not process.is_alive()
        worker = wrapper(process)
        assert worker.poll() == {"error": "perception_worker_exited", "exit_code": code}
        assert worker.poll() is None
        assert worker.count == 0
        health = worker.health()
        assert health["alive"] is False and health["exit_code"] == code
        assert health["seconds_since_result"] is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        process.close()


@pytest.mark.features("P1", "D6")
@pytest.mark.scenario("PERCEPTION-HEALTH-NO-FALSE-FRAMES")
def test_running_stopping_and_reported_errors_do_not_count_as_inference():
    worker = wrapper(SimpleNamespace(exitcode=None))
    assert worker.poll() is None
    worker.process.exitcode = 7
    worker.stop_event.set()
    assert worker.poll() is None
    worker.stop_event.clear()
    worker.outgoing.put({"error": "model_hash_mismatch"})
    assert worker.poll() == {"error": "model_hash_mismatch"}
    assert worker.count == 0
    assert worker.poll() is None
    worker = wrapper(SimpleNamespace(exitcode=None))
    worker.outgoing.put({"source": "synthetic", "objects": []})
    assert worker.poll()["effective_fps"] >= 0
    assert worker.count == 1


@pytest.mark.features("P1", "D6")
@pytest.mark.scenario("PERCEPTION-FLOW-HEALTH-COUNTERS")
def test_health_distinguishes_offers_replacement_and_completed_results():
    worker = wrapper(SimpleNamespace(exitcode=None, is_alive=lambda: True))
    assert worker.health()["seconds_since_offer"] is None
    worker.offer("synthetic", 0, 100, b"synthetic-pixels")
    worker.offer("synthetic", 0, 101, b"synthetic-pixels")
    health = worker.health()
    assert health["offered_frames"] == 2
    assert health["queue_replacements"] == 1
    assert health["received_results"] == 0
    assert health["seconds_since_offer"] >= 0
    worker.outgoing.put({"source": "synthetic"})
    worker.poll()
    health = worker.health()
    assert health["received_results"] == 1
    assert health["seconds_since_result"] >= 0
    assert "synthetic-pixels" not in str(health)
