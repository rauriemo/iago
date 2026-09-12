"""Synthetic entrypoint fixtures, including simulated origin declarations; no physical claims."""

import hashlib
import json

import pytest
from test_physical_workload import evaluate_recorded_workload
from test_workload_review import reviewed_bundle_files


@pytest.mark.features("D6", "E1", "K1", "V9", "P10")
@pytest.mark.scenario("WORKLOAD-ENTRYPOINT-EVIDENCE")
@pytest.mark.parametrize(
    "case",
    [
        "none",
        "missing",
        "component_failure",
        "component_blocked",
        "synthetic",
        "profile",
        "oversized",
        "binding",
    ],
)
def test_workload_entrypoint_retains_evidence(tmp_path, monkeypatch, case):
    variable = "IAGO_PC_WORKLOAD_MANIFEST"
    monkeypatch.delenv(variable, raising=False)
    properties = {}
    if case == "missing":
        with pytest.raises(pytest.skip.Exception, match=variable):
            evaluate_recorded_workload(properties.__setitem__)
        assert not properties
        return
    bundle = reviewed_bundle_files(
        tmp_path, case, origin="synthetic" if case == "synthetic" else "reported-real"
    )
    manifest = {
        "fixture_kind": "physical-pc-independent-recording",
        "profile": "reachy_pc" if case == "profile" else "pc",
        "bundle": bundle.model_dump(),
        **dict.fromkeys(
            ["operator", "reviewer", "recorded_at", "application_revision", "devices"],
            "synthetic declaration only",
        ),
    }
    raw = json.dumps(manifest).encode() if case != "oversized" else b" " * 65537
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    monkeypatch.setenv(variable, str(path))
    if case in {"profile", "oversized", "binding"}:
        with pytest.raises((AssertionError, ValueError)):
            evaluate_recorded_workload(properties.__setitem__)
        assert not properties
        return
    if case == "component_blocked":
        with pytest.raises(pytest.skip.Exception, match="evidence incomplete"):
            evaluate_recorded_workload(properties.__setitem__)
    elif case in {"component_failure", "synthetic"}:
        with pytest.raises(AssertionError, match="checks failed|synthetic workload"):
            evaluate_recorded_workload(properties.__setitem__)
    else:
        evaluate_recorded_workload(properties.__setitem__)
    result = properties["measurements"]
    assert result["manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["status"] == {"component_failure": "fail", "component_blocked": "blocked"}.get(
        case, "review_required"
    )
    assert properties["sample_count"] > 0
    assert not result["acceptance_pass"] and not result["physical_qualification"]
