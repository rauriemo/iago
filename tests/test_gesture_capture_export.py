"""Synthetic stream assembly retains evidence boundaries, never physical qualification."""

import json

import pytest
from test_live_thumb_scoring import dataset

from reachy_brain.evals.gesture_capture_export import export_capture
from reachy_brain.evals.gesture_observation import GestureSnapshot
from reachy_brain.evals.live_thumbs import score_live_thumbs


@pytest.mark.features("P10", "D6")
@pytest.mark.scenario("GESTURE-CAPTURE-EXPORT")
@pytest.mark.parametrize(
    "case", ["valid", "unfinished", "trailing", "count", "owner", "existing", "oversized"]
)
def test_export_validated_capture_to_actual_live_scorer(tmp_path, case):
    plan, start, end = dataset()
    rows = [
        {"type": "start", "version": 1, "physical_qualification": False},
        {"type": "sample", "observation": start.model_dump()},
        {"type": "sample", "observation": end.model_dump()},
        {"type": "complete_capture", "samples": 2, "physical_qualification": False},
    ]
    if case == "unfinished":
        rows.pop()
    if case == "trailing":
        rows.append(rows[1])
    if case == "count":
        rows[-1]["samples"] = 3
    if case == "owner":
        rows[2]["observation"]["owner"] = "other"
    source, output = tmp_path / "capture.jsonl", tmp_path / "exports"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if case == "oversized":
        source.write_bytes(b" " * (600 * 1024 + 1))
    if case == "existing":
        output.mkdir()
        (output / "start.json").write_text("preserve")
    if case == "valid":
        result = export_capture(source, output)
        assert result["snapshot_count"] == 2 and not result["physical_qualification"]
        snapshots = [
            GestureSnapshot.model_validate_json((output / name).read_bytes())
            for name in ("start.json", "end.json")
        ]
        assert score_live_thumbs(plan, *snapshots)["passes_numeric_targets"]
        assert snapshots == [start, end]
    else:
        with pytest.raises(FileExistsError if case == "existing" else ValueError):
            export_capture(source, output)
        if case == "existing":
            assert (output / "start.json").read_text() == "preserve"
        else:
            assert not output.exists()
