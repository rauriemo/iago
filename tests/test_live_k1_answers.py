"""Capture actual Astra answers; human scoring remains a separate mandatory gate."""

import asyncio
import hashlib
import json
import time

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.evals.k1_corpus import materialize
from reachy_brain.integrations.builtins import register_builtins
from reachy_brain.integrations.operations import OperationStore
from reachy_brain.integrations.registry import ActionPolicy, ToolExecutor, ToolRegistry
from reachy_brain.knowledge.index import ProjectIndex
from reachy_brain.providers.live import AstraBrain, ProviderGate
from reachy_brain.storage.notes import Notes
from reachy_brain.vision.store import VisualStore


@pytest.mark.live_provider
@pytest.mark.features("K1", "C4")
@pytest.mark.scenario("K1-ASTRA-CANDIDATE-ANSWER-CAPTURE")
async def test_capture_all_candidate_answers_for_independent_review(tmp_path, record_property):
    settings = Settings()
    if not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY missing in private setup")
    if settings.iago_development_budget == 0:
        pytest.skip("Live development budget is zero")
    corpus = await asyncio.to_thread(materialize, tmp_path / "corpus")
    index = ProjectIndex(tmp_path / "index.sqlite")
    projects = {}
    for name, root in corpus["projects"].items():
        projects[name] = index.add_project(name, root)
        await asyncio.to_thread(index.reindex, projects[name])
    cases = [
        {
            "id": f"{q['id']}:{variant}",
            "project": q["project"],
            "query": query,
            "kind": "answerable",
            "supports": q["supports"],
        }
        for q in corpus["questions"]["answerable"]
        for variant, query in enumerate(q["variants"])
    ] + [
        {
            "id": q["id"],
            "project": q["project"],
            "query": q["question"],
            "kind": "absent",
            "other_project": q["other_project"],
        }
        for q in corpus["questions"]["absent"]
    ]
    assert len(cases) == 55
    registry, policy, visual = ToolRegistry(), ActionPolicy(), VisualStore()
    notes = Notes(tmp_path / "notes.sqlite")
    register_builtins(registry, policy, visual, index, notes)
    journal = OperationStore(tmp_path / "operations.sqlite")
    executor = ToolExecutor(registry, policy, journal)
    gate = ProviderGate(settings)
    brain = AstraBrain(settings, gate)
    captured = []
    core = None

    class SyntheticVoice:
        async def stream(self, text):
            yield bytes(960)

    try:
        for case in cases:
            assert not gate.limited, "Provider plan limit reached; remaining cases were not run"
            index.activate(projects[case["project"]])
            events = []
            owner = {}

            async def send(message, events=events, owner=owner):
                current = owner["core"]
                if message["type"] != "audio":
                    events.append(message)
                if message["type"] == "audio" and current.speech:
                    current.speech.acknowledge(message["epoch"], message["sequence"])
                elif message["type"] == "segment_end":
                    await current.heard(message["epoch"], message["segment"])

            core = Conversation(
                settings, brain, {"openai": SyntheticVoice()}, executor, visual, send
            )
            core.project_context = lambda: index.active
            owner["core"] = core
            core.mode = "conversation"
            start = time.monotonic()
            await core.user_turn("Using only the active project's documents: " + case["query"])
            await asyncio.wait_for(core.task, 90)
            answers = [e["text"] for e in events if e["type"] == "answer"]
            evidence = {
                row["id"]: row
                for event in events
                if event["type"] == "evidence"
                for row in event["frames"]
                if "project_id" in row
            }
            record = {
                **case,
                "answer": "\n".join(answers),
                "evidence": list(evidence.values()),
                "errors": [e for e in events if e["type"] == "error"],
                "seconds": time.monotonic() - start,
                "review": "pending independent human fact/citation/abstention review",
            }
            record["sha256"] = hashlib.sha256(
                json.dumps(record, sort_keys=True).encode()
            ).hexdigest()
            captured.append(record)
            assert answers and not record["errors"], f"Incomplete capture: {case['id']}"
            for row in evidence.values():
                assert row["project_id"] == projects[case["project"]]
                assert index.read(index.active, [row["id"]])[0] == row
            await core.stop()
        assert len(captured) == 55
    finally:
        record_property("sample_count", len(captured))
        record_property(
            "expected",
            "Capture 55 actual Astra responses and validate returned evidence identity; human answer-quality scoring remains pending",
        )
        record_property(
            "measurements",
            {
                "model": settings.brain_model,
                "fixture": corpus["manifest"],
                "audio": "synthetic voice and playout acknowledgments; no physical qualification",
                "quality_status": "not_assessed",
                "answers": captured,
                "usage_attempts": len(gate.usage),
            },
        )
        if core:
            await core.stop()
        await executor.close()
        await brain.close()
        journal.close()
