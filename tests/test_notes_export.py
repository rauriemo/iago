"""Local note persistence/export checks; no external services or personal fixtures."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from reachy_brain.config import Settings
from reachy_brain.integrations.registry import ToolError
from reachy_brain.storage.notes import Notes
from reachy_brain.web.app import create_app


@pytest.mark.features("C9", "D4")
@pytest.mark.scenario("NOTES-EXPORT-DELETE-ISOLATION")
def test_unicode_download_is_private_read_only_and_delete_is_scoped(tmp_path):
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path), token="test")
    ) as client:
        headers = {"Authorization": "Bearer test"}
        text = "Ideia: ação\n<script>plain text only</script>"
        ids = [
            client.post(
                "/api/notes", headers=headers, json={"action": "save", "text": value}
            ).json()["id"]
            for value in [text, "Keep this note"]
        ]
        url = f"/api/notes/{ids[0]}/export"
        assert client.get(url).status_code == 401
        export = client.get(url, headers=headers)
        assert export.content == text.encode("utf-8")
        assert export.headers["content-type"].startswith("text/plain")
        assert export.headers["content-disposition"].startswith("attachment;")
        assert export.headers["cache-control"] == "no-store"
        assert client.get("/api/notes", headers=headers).json()["total"] == 2
        assert (
            client.post(
                "/api/notes", headers=headers, json={"action": "delete", "id": ids[0]}
            ).status_code
            == 200
        )
        assert client.get(url, headers=headers).status_code == 409
        assert client.get(f"/api/notes/{ids[1]}/export", headers=headers).text == "Keep this note"


@pytest.mark.features("C9", "D5")
@pytest.mark.scenario("NOTES-BOUNDS-PAGINATION")
def test_bounded_saves_are_atomic_and_pages_do_not_hide_older_notes(tmp_path):
    notes = Notes(tmp_path / "notes.sqlite", max_notes=1)

    def save(text):
        try:
            return notes.save(text)
        except ToolError as exc:
            assert exc.code == "notes_storage_limit"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first", "second"]))
    assert len([r for r in results if r]) == 1 and notes.count() == 1
    with pytest.raises(ToolError, match="invalid_note"):
        notes.save("x" * 12001)
    larger = Notes(tmp_path / "pages.sqlite")
    expected = {larger.save(str(i)) for i in range(101)}
    first, second = larger.list(), larger.list(100)
    assert len(first) == 100 and len(second) == 1
    assert {row["id"] for row in first + second} == expected
