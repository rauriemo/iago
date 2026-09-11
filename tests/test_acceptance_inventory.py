"""Inventory checks validate reporting only, never the referenced product gates."""

import hashlib
from pathlib import Path

import pytest

from reachy_brain.evals.requirements import acceptance_inventory


@pytest.mark.features("D5")
@pytest.mark.scenario("EVIDENCE-ACCEPTANCE-INVENTORY")
def test_inventory_preserves_required_gate_text_and_does_not_infer_passes():
    inventory = acceptance_inventory()
    by_id = {r["id"]: r for r in inventory["requirements"]}
    required = {*(f"GATE-{i}" for i in range(1, 9)), "MIXED-WORKLOAD"}
    for prefix, suffixes in {
        "P10": "MEDIA POLICY UI",
        "V9": "GROUNDING LIFECYCLE CANCEL",
        "K1": "RETRIEVE CITE ABSENT UPDATE BOUNDARIES UI",
        "E1": "TRANSPORT REGISTRY SCHEMA ACCOUNTS SKILLS CONFIRM LIFECYCLE RECONCILE INJECTION EVENTS PRIORITY",
    }.items():
        required.update(prefix + "-" + suffix for suffix in suffixes.split())
    assert required <= by_id.keys()
    assert len([key for key in by_id if key.startswith("SPEC18-")]) == 34
    assert len(by_id) == 68
    assert "30 supported files total" in by_id["K1-CORPUS"]["requirement"]
    assert "alternating transports" in by_id["E1-MIXED-WORKLOAD"]["requirement"]
    assert ">=90% recall" in by_id["P10-MEDIA"]["requirement"]
    assert "at least 41/45" in by_id["K1-RETRIEVE"]["requirement"]
    assert "at least 60 minutes" in by_id["MIXED-WORKLOAD"]["requirement"]
    assert "both with and without provider idempotency" in by_id["E1-RECONCILE"]["requirement"]
    for source, metadata in inventory["sources"].items():
        assert metadata["sha256"] == hashlib.sha256(Path(source).read_bytes()).hexdigest()
    for record in by_id.values():
        assert record["coverage_status"] == "not_assessed" and record["qualifying_evidence"] == []
        line = Path(record["source"]).read_text(encoding="utf-8").splitlines()[record["line"] - 1]
        assert record["title"] in line or record["id"] in line or record["requirement"] == line


@pytest.mark.features("D5")
@pytest.mark.scenario("EVIDENCE-MISSING-ACCEPTANCE-SOURCES")
def test_missing_source_is_unavailable_not_empty_coverage_pass(tmp_path):
    inventory = acceptance_inventory(tmp_path)
    assert inventory["status"] == "unavailable"
    assert inventory["requirements"] == []
    assert all(s["status"] == "unavailable" for s in inventory["sources"].values())
