"""Reviewed PC workload artifact checks; a passing check is not physical qualification."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from test_physical_opening_words import validate_profile

from reachy_brain.evals.workload_review import ReviewedWorkloadBundle, score_reviewed_bundle


def evaluate_recorded_workload(record_property):
    variable = "IAGO_PC_WORKLOAD_MANIFEST"
    configured = os.environ.get(variable)
    if not configured or not Path(configured).is_file():
        pytest.skip(variable + " required; see docs/iago/WORKLOAD_CAPTURE.md")
    path = Path(configured).resolve()
    with path.open("rb") as source:
        data = source.read(65537)
    assert len(data) <= 65536, "workload manifest size limit"
    manifest = json.loads(data)
    validate_profile(manifest, "pc")
    assert manifest.get("profile") == "pc", "workload deployment profile mismatch"
    for key in ("operator", "reviewer", "recorded_at", "application_revision", "devices"):
        assert manifest.get(key), "missing " + key
    result = score_reviewed_bundle(
        path.parent, ReviewedWorkloadBundle.model_validate(manifest["bundle"])
    )
    record_property("sample_count", result["reviewed_activity_count"])
    record_property("measurements", {"manifest_sha256": hashlib.sha256(data).hexdigest(), **result})
    assert result["coverage"]["origin"] == "reported-real", "synthetic workload cannot qualify"
    assert result["status"] != "fail", "workload component checks failed"
    if result["status"] == "blocked":
        pytest.skip("workload component evidence incomplete; inspect measurements")
    assert result["status"] == "review_required" and result["declared_review_bound"]


@pytest.mark.live_pc
@pytest.mark.features("D6", "E1", "K1", "V9", "P10")
@pytest.mark.scenario("PC-WORKLOAD-REVIEWED-BUNDLE")
def test_pc_reviewed_workload_bundle(record_property):
    evaluate_recorded_workload(record_property)
