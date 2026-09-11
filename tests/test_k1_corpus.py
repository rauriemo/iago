"""Frozen synthetic candidate corpus; gold labels still require human review."""

import json
from collections import Counter

import pytest

from reachy_brain.evals.k1_corpus import materialize
from reachy_brain.knowledge.index import ProjectIndex


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-CANDIDATE-CORPUS-BOUNDARIES")
def test_full_candidate_corpus_formats_and_boundary_statuses(tmp_path):
    corpus = materialize(tmp_path / "corpus")
    index = ProjectIndex(tmp_path / "index.sqlite")
    assert len(corpus["documents"]) == 30
    for project, root in corpus["projects"].items():
        categories = Counter(d["category"] for d in corpus["documents"] if d["project"] == project)
        assert categories == {"markdown": 2, "text": 2, "pdf": 2, "docx": 2, "source": 2}
        for record in (d for d in corpus["documents"] if d["project"] == project):
            result = index._parse(root / record["path"])
            assert result["status"] == "indexed", record["id"]
            if record["category"] == "pdf":
                assert {p["locator"] for p in result["passages"]} == {"1", "2", "3"}
            if record["category"] == "docx":
                assert len({p["locator"].split(" / ")[0] for p in result["passages"]}) == 3
    assert len(corpus["extras"]) == 12
    for record in corpus["extras"]:
        root = corpus["projects"]["aster"]
        path = root / record["path"]
        result = (
            index._parse(path)
            if index._allowed(root, path)
            else {"status": "excluded", "passages": []}
        )
        assert result["status"] == record["status"], record
        assert "DUMMY_SYNTHETIC_K1_SECRET" not in str(result["passages"])
        assert "OUTSIDE_ROOT_SENTINEL" not in str(result["passages"])
        assert "TRAVERSAL_SENTINEL" not in str(result["passages"])
    questions = corpus["questions"]
    assert len(questions["answerable"]) == 30
    assert sum(len(q["variants"]) for q in questions["answerable"]) == 45
    assert sum(len(q["supports"]) == 2 for q in questions["answerable"]) == 6
    assert len(questions["absent"]) == 10
    assert sum(q["other_project"] is not None for q in questions["absent"]) >= 4


@pytest.mark.features("K1")
@pytest.mark.scenario("K1-CANDIDATE-RETRIEVAL-CITATIONS")
def test_candidate_queries_retrieve_all_gold_support_with_valid_citations(
    tmp_path, record_property
):
    corpus = materialize(tmp_path / "corpus")
    index = ProjectIndex(tmp_path / "index.sqlite")
    projects = {}
    for name, root in corpus["projects"].items():
        projects[name] = index.add_project(name, root)
        index.reindex(projects[name])
    scores = []
    parsed = {}
    for question in corpus["questions"]["answerable"]:
        project = projects[question["project"]]
        index.activate(project)
        for variant, query in enumerate(question["variants"]):
            hits = index.search(project, query)
            found = all(
                any(
                    hit["path"] == gold["path"]
                    and hit["locator_kind"] == gold["locator_kind"]
                    and hit["locator"] == gold["locator"]
                    and gold["fact"] in hit["text"]
                    for hit in hits
                )
                for gold in question["supports"]
            )
            scores.append(
                {
                    "question": question["id"],
                    "variant": variant,
                    "hit": found,
                    "returned": [f"{h['path']}#{h['locator']}" for h in hits],
                }
            )
            for hit in hits:
                assert hit["project_id"] == project
                source = corpus["projects"][question["project"]] / hit["path"]
                assert source.is_file() and hit["revision"] == index._revision(source)
                if source not in parsed:
                    parsed[source] = index._parse(source)
                assert any(
                    p["locator_kind"] == hit["locator_kind"]
                    and p["locator"] == hit["locator"]
                    and hit["text"] in p["text"]
                    for p in parsed[source]["passages"]
                )
    total = sum(s["hit"] for s in scores)
    paraphrases = sum(s["hit"] for s in scores if s["variant"] == 1)
    measurements = {
        "fixture": corpus["manifest"]["version"],
        "label_review": "pending human review",
        "hits": total,
        "variants": len(scores),
        "paraphrase_hits": paraphrases,
        "paraphrases": 15,
        "queries": scores,
    }
    record_property("measurements", measurements)
    record_property("sample_count", 45)
    record_property(
        "expected",
        "At least 41/45 full-support hits and 14/15 paraphrase hits; every returned citation resolves truthfully",
    )
    record_property("observed", f"{total}/45 full-support hits; {paraphrases}/15 paraphrase hits")
    assert total >= 41 and paraphrases >= 14, json.dumps(measurements)
