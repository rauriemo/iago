"""Recorded live-output scoring; missing independent review is blocked, never pass."""

import os

import pytest

from reachy_brain.evals.k1_review import read_bounded, score_review


@pytest.mark.live_provider
@pytest.mark.features("K1")
@pytest.mark.scenario("K1-HUMAN-REVIEWED-ANSWER-QUALITY")
def test_independently_reviewed_document_answers(record_property):
    capture = os.environ.get("IAGO_K1_CAPTURE_REPORT")
    review = os.environ.get("IAGO_K1_HUMAN_REVIEW")
    if not capture or not review:
        pytest.skip(
            "Set IAGO_K1_CAPTURE_REPORT and IAGO_K1_HUMAN_REVIEW to the exact captured report and independent human review"
        )
    result = score_review(read_bounded(capture), read_bounded(review))
    record_property("sample_count", 55)
    record_property("measurements", result)
    assert result["status"] == "pass", result["failures"]
