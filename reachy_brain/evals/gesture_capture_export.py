"""Validate a captured gesture stream and export its exact observation endpoints."""

import hashlib
import json
from pathlib import Path

from .gesture_observation import GestureSnapshot, compare_gestures
from .speech_observation import validate_snapshot


def export_capture(source: Path, destination: Path):
    digest = hashlib.sha256()
    size = count = 0
    first = previous = None
    first_raw = last_raw = None
    complete = False
    with source.open("rb") as stream:
        for index in range(7204):
            line = stream.readline(600 * 1024 + 1)
            if not line:
                break
            size += len(line)
            if len(line) > 600 * 1024 or size > 32 * 1024 * 1024:
                raise ValueError("gesture_capture_file_limit")
            digest.update(line)
            row = json.loads(line)
            if not isinstance(row, dict) or complete:
                raise ValueError("invalid_gesture_capture_record")
            if index == 0:
                if (
                    row.get("type") != "start"
                    or row.get("version") != 1
                    or row.get("physical_qualification") is not False
                ):
                    raise ValueError("gesture_capture_header_required")
                continue
            if row.get("type") == "sample":
                current = GestureSnapshot.model_validate(row.get("observation"))
                validate_snapshot(current)
                if previous is None:
                    first, first_raw = current, row["observation"]
                else:
                    compare_gestures(previous, current)
                previous, last_raw = current, row["observation"]
                count += 1
            elif row.get("type") == "complete_capture":
                if (
                    type(row.get("samples")) is not int
                    or row["samples"] != count
                    or row.get("physical_qualification") is not False
                ):
                    raise ValueError("gesture_capture_completion_mismatch")
                complete = True
            else:
                raise ValueError("gesture_capture_incomplete")
        else:
            raise ValueError("gesture_capture_record_limit")
    if not complete or count < 2:
        raise ValueError("gesture_capture_incomplete")
    # The current bound evaluator consumes two exports. Never discard earlier
    # interval events just because an intermediate poll once observed them.
    observation = compare_gestures(first, previous)
    exports = {
        name: json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n"
        for name, value in (("start.json", first_raw), ("end.json", last_raw))
    }
    if any(len(value.encode()) > 512 * 1024 for value in exports.values()):
        raise ValueError("gesture_endpoint_export_limit")
    destination.mkdir(exist_ok=False)
    for name, value in exports.items():
        with (destination / name).open("x", encoding="utf-8") as output:
            output.write(value)
    result = {
        "capture_sha256": digest.hexdigest(),
        "snapshot_count": count,
        "observation": observation,
        "physical_qualification": False,
        "scope": "Validated captured endpoint exports only; frozen live labels and independent recording review remain required.",
    }
    with (destination / "capture-binding.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    export_capture(args.capture, args.output_directory)
    print("Validated observation endpoints exported; no physical qualification claimed.")


if __name__ == "__main__":
    main()
