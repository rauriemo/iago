"""Real authenticated HTTP; malformed controls cannot mutate the visual store."""

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.web.app import create_app


@pytest.mark.features("V1", "V3", "V9", "D5")
@pytest.mark.scenario("VISUAL-CONTROL-REQUEST-VALIDATION")
def test_bounded_invalid_controls_leave_sources_unchanged(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    visual = app.state.visual
    source = visual.source("synthetic", "camera", "Existing")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test"}
        for path in ("/api/source", "/api/visual"):
            assert client.post(path, content=b"{" * 5000).status_code == 401
            assert client.post(path, headers=headers, content=b" " * 4097).status_code == 413
            for raw in (b"[]", b"null", b"{", b'"text"'):
                assert client.post(path, headers=headers, content=raw).status_code == 400
        for payload in (
            {},
            {"kind": []},
            {"kind": "camera", "label": []},
            {"kind": "screen", "label": "x" * 121},
            {"kind": "camera", "label": "\ud800"},
        ):
            import json

            response = client.post("/api/source", headers=headers, content=json.dumps(payload))
            assert response.status_code == 400
        for payload in (
            {},
            {"action": []},
            {"action": "pin", "frame": []},
            {"action": "pin", "frame": "f", "label": []},
            {"action": "pin", "frame": "f", "label": "x" * 101},
            {"action": "pin", "frame": "f", "label": "\ud800"},
            {"action": "clear", "source": []},
        ):
            response = client.post("/api/visual", headers=headers, content=json.dumps(payload))
            assert response.status_code == 400
        assert visual.sources == {source.id: source}
        assert source.enabled and source.generation == 0
        assert visual.context_generation == 0
