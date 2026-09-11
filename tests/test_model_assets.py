"""Synthetic HTTP streams and actual filesystem publication; no camera qualification."""

import hashlib
from contextlib import contextmanager

import pytest

from reachy_brain.vision import detectors


@pytest.mark.features("D1", "D5", "P1", "P2")
@pytest.mark.scenario("PERCEPTION-ASSET-ATOMIC-INSTALL")
@pytest.mark.parametrize(
    "outcome", ["success", "interrupted", "oversize", "truncated", "corrupt", "race"]
)
def test_asset_install_preserves_existing_and_removes_partial(tmp_path, monkeypatch, outcome):
    data = b"locked-model"
    item = {
        "url": "https://example.invalid/model",
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    destination = tmp_path / "model.task"

    class Response:
        def raise_for_status(self):
            pass

        def iter_bytes(self, chunk_size):
            assert chunk_size == 65536
            assert not destination.exists()
            if outcome == "race":
                destination.write_bytes(b"existing-user-file")
            yield data[:3]
            assert not destination.exists() or outcome == "race"
            if outcome == "interrupted":
                raise OSError("synthetic disconnect")
            yield {
                "oversize": data[3:] + b"!",
                "truncated": b"",
                "corrupt": b"x" * (len(data) - 3),
            }.get(outcome, data[3:])

    @contextmanager
    def stream(*args, **kwargs):
        yield Response()

    monkeypatch.setattr(detectors.httpx, "stream", stream)
    if outcome == "success":
        detectors.download_asset(destination, item)
        assert destination.read_bytes() == data
    else:
        with pytest.raises((OSError, ValueError)):
            detectors.download_asset(destination, item)
        if outcome == "race":
            assert destination.read_bytes() == b"existing-user-file"
        else:
            assert not destination.exists()
    assert not list(tmp_path.glob(".model-*"))


@pytest.mark.features("D1", "D5", "P1", "P2")
@pytest.mark.scenario("PERCEPTION-ASSET-MISSING-STATUS")
def test_missing_models_worker_reports_actionable_status(tmp_path):
    import queue
    import threading

    from reachy_brain.vision.worker import run_worker

    outgoing = queue.Queue()
    run_worker(queue.Queue(), outgoing, threading.Event(), tmp_path)
    assert outgoing.get_nowait() == {"error": "perception_models_missing"}
