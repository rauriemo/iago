"""Recorded PC document evidence; no provider or physical-device calls."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from test_physical_opening_words import validate_profile

from reachy_brain.evals.k1_pc_bundle import K1PCBundle, score_pc_bundle


def evaluate_pc_documents(record_property):
    configured = os.environ.get("IAGO_PC_K1_MANIFEST")
    if not configured or not Path(configured).is_file():
        pytest.skip("IAGO_PC_K1_MANIFEST required; see docs/iago/K1_REVIEW.md")
    path = Path(configured).resolve()
    with path.open("rb") as source:
        raw = source.read(65537)
    assert len(raw) <= 65536, "PC document manifest size limit"
    manifest = json.loads(raw)
    validate_profile(manifest, "pc")
    assert manifest.get("profile") == "pc", "PC document profile mismatch"
    for key in ("operator", "application_revision", "devices"):
        assert manifest.get(key), "missing " + key
    result = score_pc_bundle(path.parent, K1PCBundle.model_validate(manifest["bundle"]))
    record_property("sample_count", result["query_count"])
    record_property("measurements", {"manifest_sha256": hashlib.sha256(raw).hexdigest(), **result})
    assert result["status"] == "review_required", result["failures"]


@pytest.mark.live_pc
@pytest.mark.features("K1", "D4", "C4")
@pytest.mark.scenario("PC-K1-REVIEWED-QUERY-BUNDLE")
def test_pc_reviewed_document_query_bundle(record_property):
    evaluate_pc_documents(record_property)
