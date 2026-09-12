"""Private reviewed controlled-entry bundles; synthetic data cannot qualify a physical run."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from test_physical_opening_words import validate_profile

from reachy_brain.evals.presence_bundle import PresenceBundle, score_bundle


def evaluate_recorded_presence(record_property, profile):
    variable = "IAGO_" + profile.upper() + "_ENTRY_MANIFEST"
    configured = os.environ.get(variable)
    if not configured or not Path(configured).is_file():
        pytest.skip(variable + " required; see docs/iago/WAVE_EVAL.md")
    path = Path(configured).resolve()
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    assert len(data) <= 1024 * 1024, "controlled entry manifest size limit"
    manifest = json.loads(data)
    validate_profile(manifest, profile)
    for key in ("operator", "reviewer", "recorded_at", "application_revision", "devices"):
        assert manifest.get(key), "missing " + key
    result = score_bundle(
        path.parent, PresenceBundle.model_validate(manifest["bundle"]), profile=profile
    )
    record_property("sample_count", len(result["cases"]))
    record_property("measurements", {"manifest_sha256": hashlib.sha256(data).hexdigest(), **result})
    assert result["passes_numeric_targets"], "controlled entry numeric/context gates failed"


@pytest.mark.live_pc
@pytest.mark.features("P1", "P4", "P6", "D5", "D6")
@pytest.mark.scenario("PC-CONTROLLED-ENTRY-REVIEWED-DATASET")
def test_pc_controlled_entry_dataset(record_property):
    evaluate_recorded_presence(record_property, "pc")


@pytest.mark.robot
@pytest.mark.features("P1", "P4", "P6", "D2", "D3", "D5", "D6")
@pytest.mark.scenario("ROBOT-CONTROLLED-ENTRY-REVIEWED-DATASET")
@pytest.mark.parametrize("profile", ["reachy_pc", "reachy_local"])
def test_robot_controlled_entry_dataset(record_property, profile):
    evaluate_recorded_presence(record_property, profile)
