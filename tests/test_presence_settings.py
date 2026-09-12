"""Synthetic presence timelines exercise saved settings through the actual HTTP app."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.vision.events import PerceptionEvents
from reachy_brain.web import app as web_app


@pytest.mark.features("P1", "P5", "P6", "D4")
@pytest.mark.scenario("PRESENCE-CONFIGURED-TIMELINE")
def test_saved_presence_timings_reach_interpreter_and_survive_source_restart(tmp_path, monkeypatch):
    interpreters = []

    def interpreter():
        value = PerceptionEvents()
        interpreters.append(value)
        return value

    monkeypatch.setattr(web_app, "PerceptionEvents", interpreter)
    settings = Settings(_env_file=None, data_dir=tmp_path, perception_enabled=False)
    headers = {"Authorization": "Bearer test"}

    def observe(value, source, at, visible):
        result = {
            "source": source.id,
            "generation": source.generation,
            "captured": at,
            "objects_at": at,
            "objects": [{"label": "person", "confidence": 0.95, "box": [0, 0, 1, 1]}]
            if visible
            else [],
            "hands": [],
            "moving": False,
            "detector": "synthetic",
        }
        events, _ = value.update(result, source, now=at)
        return [
            (event.kind, event.duration) for event in events if event.kind.startswith("person_")
        ]

    for restart in range(2):
        with TestClient(web_app.create_app(settings, token="test")) as client:
            data = client.get("/api/behaviors", headers=headers).json()["configuration"]
            if not restart:
                assert [
                    data[name]
                    for name in ("presence_confirmation", "presence_absence", "presence_rearm")
                ] == [0.7, 3, 15]
                data.update(presence_confirmation=1.5, presence_absence=2.0, presence_rearm=5.0)
                assert client.post("/api/behaviors", headers=headers, json=data).status_code == 200
            else:
                assert data["presence_confirmation"] == 1.5
                assert data["presence_absence"] == 2.0
                assert data["presence_rearm"] == 5.0
            value = interpreters[-1]
            source = SimpleNamespace(id="camera", kind="camera", generation=1, enabled=True)
            for generation in (1, 2):
                source.generation = generation
                offset = generation * 100
                assert observe(value, source, offset, True) == []  # Startup is not arrival.
                assert observe(value, source, offset + 1, False) == []
                assert observe(value, source, offset + 2.9, False) == []
                assert observe(value, source, offset + 3, False) == [("person_left_view", 2.0)]
                assert observe(value, source, offset + 6, True) == []
                assert observe(value, source, offset + 7, True) == []
                assert observe(value, source, offset + 7.5, True) == [("person_entered_view", 1.5)]
            saved = (tmp_path / "behaviors.json").read_bytes()
            invalid = dict(data, presence_absence=-1.0)
            assert client.post("/api/behaviors", headers=headers, json=invalid).status_code == 400
            assert (tmp_path / "behaviors.json").read_bytes() == saved
            assert value.presence.absence == 2.0
