"""Actual Astra on synthetic canonical text; narrow fact retention, not general quality."""

import pytest

from reachy_brain.config import Settings
from reachy_brain.core.conversation import Conversation
from reachy_brain.providers.live import AstraBrain


@pytest.mark.live_provider
@pytest.mark.features("C4", "C8")
@pytest.mark.scenario("ASTRA-CANONICAL-SUMMARY")
async def test_canonical_summary_retains_synthetic_decision_and_uncertainty(record_property, live_gate):
    settings = Settings()
    if not settings.openai_api_key.get_secret_value():
        pytest.skip("OPENAI_API_KEY missing in private setup")
    if settings.iago_development_budget == 0:
        pytest.skip("Live development budget is zero")
    gate = live_gate
    brain = AstraBrain(settings, gate)

    async def send(event):
        pass

    core = Conversation(settings, brain, {}, None, None, send)
    core.mode = "conversation"
    core.history.append(
        {
            "role": "user",
            "content": "For this fictional project, I choose blue for the dome. The budget ceiling is 120 dollars. The supplier is still undecided. Keep these three details in memory.",
        }
    )
    for n in range(1, 26):
        core.history.append(
            {
                "role": "assistant" if n % 2 else "user",
                "content": "We are continuing to discuss this fictional project; no further decisions or external actions.",
            }
        )
    recent = list(core.history)[-8:]
    try:
        await core.compact_history(core.epoch)
        assert len(core.history) == 9, "Astra summary unavailable or rejected"
        summary = core.history[0]["content"].lower()
        assert "blue" in summary and "120" in summary and "supplier" in summary
        assert any(
            term in summary for term in ("undecided", "unresolved", "not yet", "not decided")
        )
        assert list(core.history)[1:] == recent
        assert brain.settings.brain_model == "gpt-6-astra"
        assert len(gate.usage) == 1
        record_property("sample_count", 1)
        record_property(
            "measurements",
            {
                "fixture": "synthetic fictional conversation",
                "source_messages": 18,
                "recent_messages": 8,
                "summary_characters": len(summary),
                "model": brain.settings.brain_model,
                "usage_attempts": len(gate.usage),
            },
        )
    finally:
        await brain.close()
