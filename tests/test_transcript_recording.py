"""Synthetic conversation text, real SQLite and authenticated local HTTP; no audio recording."""

import asyncio
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.storage.recorder import TranscriptRecorder
from reachy_brain.storage.transcripts import Transcripts
from reachy_brain.vision.store import VisualStore
from reachy_brain.web.app import create_app


@pytest.mark.features("C9", "C8", "C2", "D5")
@pytest.mark.scenario("TRANSCRIPT-HEARD-LOCAL-STOP")
async def test_slow_storage_does_not_block_stop_and_unheard_text_is_not_saved(tmp_path):
    store = Transcripts(tmp_path / "transcripts.sqlite")
    entered, release = threading.Event(), threading.Event()
    original = store.record

    def slow_record(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    store.record = slow_record
    recorder = TranscriptRecorder(store)
    await recorder.start()
    await recorder.change("enabled", True)
    sent = []

    async def send(message):
        sent.append(message)

    core = Conversation(
        Settings(_env_file=None),
        None,
        {},
        ToolExecutor(ToolRegistry(), ActionPolicy(), None),
        VisualStore(),
        send,
    )
    core.record = recorder.submit
    core.record_snapshot = recorder.snapshot
    core.answer_recording_token = recorder.snapshot()
    core.mode = "conversation"
    core.ledger.add(0, "a", "Heard first.")
    core.ledger.add(0, "b", "Unheard second.")
    try:
        await core.heard(0, "a")
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.wait_for(core.stop(), 0.1)
        assert sent[-1]["type"] == "stop"
        await core.heard(0, "b")
        release.set()
        await recorder.queue.join()
        rows = await asyncio.to_thread(store.entries, core.session)
        assert len(rows) == 1 and rows[0]["text"] == "Heard first."
        assert rows[0]["metadata"] == {
            "epoch": 0,
            "profile": "desktop",
            "mode": "conversation",
            "session_started": core.session_started,
        }
    finally:
        release.set()
        await recorder.close()


@pytest.mark.features("C9", "D4", "D5")
@pytest.mark.scenario("TRANSCRIPT-PRIVATE-EXPORT-CONTROLS")
def test_private_opt_in_export_and_scoped_deletion(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test"}
        assert client.get("/api/transcripts").status_code == 401
        assert not client.get("/api/transcripts", headers=headers).json()["enabled"]
        assert (
            client.post(
                "/api/transcripts", headers=headers, json={"action": "enabled", "enabled": "yes"}
            ).status_code
            == 400
        )
        client.post(
            "/api/transcripts", headers=headers, json={"action": "enabled", "enabled": True}
        ).raise_for_status()
        session, other = uuid.uuid4().hex, uuid.uuid4().hex
        for key in (session, other):
            assert client.portal.call(
                app.state.recorder.submit, key, "u", "user", "Ideia: ação <script>", "typed"
            )
        client.portal.call(app.state.recorder.queue.join)
        url = f"/api/transcripts/{session}/export"
        assert client.get(url).status_code == 401
        response = client.get(url, headers=headers)
        assert response.text == "user (typed):\nIdeia: ação <script>"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["cache-control"] == "no-store"
        assert client.get(url + "?format=json").status_code == 401
        structured = client.get(url + "?format=json", headers=headers)
        structured.raise_for_status()
        exported = structured.json()
        assert exported["format_version"] == 1 and exported["session"] == session
        assert len(exported["entries"]) == 1
        assert exported["entries"][0]["entry"] == "u"
        assert exported["entries"][0]["text"] == "Ideia: ação <script>"
        assert exported["entries"][0]["metadata"] == {}
        assert structured.headers["cache-control"] == "no-store"
        assert structured.headers["content-type"] == "application/json"
        assert structured.headers["content-disposition"].endswith('.json"')
        assert client.get(url + "?format=html", headers=headers).status_code == 400
        store = Transcripts(tmp_path / "transcripts.sqlite")
        metadata = {
            "epoch": 1,
            "generated_text": "Read label: AX7. More unheard words.",
            "interrupted": True,
            "evidence_refs": [
                {
                    "kind": "visual",
                    "id": "frame-1",
                    "source_id": "camera-1",
                    "source_generation": 2,
                    "captured": 100.0,
                    "image_sha256": "a" * 64,
                    "capture_time_known": True,
                    "source_kind": "camera",
                    "region": [1, 2, 10, 20],
                }
            ],
        }
        store.record(
            session,
            "a1",
            "assistant",
            "Read label: AX7.",
            "heard",
            generation=store.state()["generation"],
            metadata=metadata,
        )
        answer = client.get(url + "?format=json", headers=headers).json()["entries"][-1]
        assert answer["entry"] == "a1" and answer["text"] == "Read label: AX7."
        assert answer["metadata"] == metadata
        client.post(
            "/api/transcripts", headers=headers, json={"action": "delete", "session": session}
        ).raise_for_status()
        assert client.get(url, headers=headers).status_code == 404
        assert client.get(url + "?format=json", headers=headers).status_code == 404
        assert len(client.get("/api/transcripts", headers=headers).json()["sessions"]) == 1
        client.post(
            "/api/transcripts", headers=headers, json={"action": "enabled", "enabled": False}
        ).raise_for_status()
        assert not client.portal.call(
            app.state.recorder.submit, other, "u2", "user", "Unsaved", "typed"
        )
        client.post(
            "/api/transcripts", headers=headers, json={"action": "delete_all"}
        ).raise_for_status()
        assert client.get("/api/transcripts", headers=headers).json()["entries"] == 0
