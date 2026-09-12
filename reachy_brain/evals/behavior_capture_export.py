"""Validate a captured behavior stream and export its complete observation sequence."""

import hashlib
import json
from pathlib import Path

from .behavior_observation import BehaviorSnapshot, compare_decisions, validate


def export_capture(source: Path, destination: Path):
    digest = hashlib.sha256()
    size = count = 0
    previous = None
    snapshots = []
    records = 0
    complete = False
    with source.open("rb") as stream:
        for index in range(3605):
            line = stream.readline(600 * 1024 + 1)
            if not line:
                break
            size += len(line)
            if len(line) > 600 * 1024 or size > 32 * 1024 * 1024:
                raise ValueError("behavior_capture_file_limit")
            digest.update(line)
            row = json.loads(line)
            if not isinstance(row, dict) or complete:
                raise ValueError("invalid_behavior_capture_record")
            if index == 0:
                if (
                    row.get("type") != "start"
                    or type(row.get("version")) is not int
                    or row.get("version") != 1
                    or row.get("physical_qualification") is not False
                ):
                    raise ValueError("behavior_capture_header_required")
                continue
            if row.get("type") == "sample":
                current = BehaviorSnapshot.model_validate(row.get("observation"))
                validate(current)
                if previous is not None:
                    records += len(compare_decisions(previous, current)["records"])
                    if records > 20000:
                        raise ValueError("behavior_record_limit")
                previous = current
                snapshots.append(row["observation"])
                count += 1
                if count > 3602:
                    raise ValueError("behavior_snapshot_count")
            elif row.get("type") == "complete_capture":
                if (
                    type(row.get("samples")) is not int
                    or row["samples"] != count
                    or row.get("physical_qualification") is not False
                ):
                    raise ValueError("behavior_capture_completion_mismatch")
                complete = True
            else:
                raise ValueError("behavior_capture_incomplete")
        else:
            raise ValueError("behavior_capture_record_limit")
    if not complete or count < 2:
        raise ValueError("behavior_capture_incomplete")
    content = json.dumps(snapshots, separators=(",", ":"), allow_nan=False) + "\n"
    if len(content.encode()) > 32 * 1024 * 1024:
        raise ValueError("behavior_snapshot_export_limit")
    destination.mkdir(exist_ok=False)
    with (destination / "snapshots.json").open("xb") as output:
        output.write(content.encode("utf-8"))
    result = {
        "capture_sha256": digest.hexdigest(),
        "snapshot_count": count,
        "decision_record_count": records,
        "snapshots_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "record_coverage_complete": True,
        "physical_qualification": False,
        "scope": "Validated captured snapshot sequence only; independent recording, duration and review remain required.",
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
    print("Validated observation sequence exported; no physical qualification claimed.")


if __name__ == "__main__":
    main()
