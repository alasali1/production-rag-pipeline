"""Step 5 QA: cross-encoder score normalization and confidence gating (Tests 2 and 3)."""

from __future__ import annotations

import math
from typing import List

import pytest

from src.reranker import CrossEncoderReranker, sigmoid
from src.schemas import RerankStatus, RetrievedChunk
from tests.test_pipeline import FakeCrossEncoder

THRESHOLD = 0.55  # production default (Settings.confidence_threshold)


def logit(p: float) -> float:
    """Inverse sigmoid: the raw cross-encoder score that normalizes to probability p."""
    return math.log(p / (1.0 - p))


def candidates(n: int) -> List[RetrievedChunk]:
    return [RetrievedChunk(chunk_id=f"d.md#s{i}", content=f"c{i}", score=0.5, source="internal_doc") for i in range(n)]


# ---------- Test 2: sigmoid logit normalization ----------

# Realistic ms-marco cross-encoder logits span roughly -12..+12 (measured: +7.2 relevant, -11.4 irrelevant).
REALISTIC_LOGITS = [-12.0, -11.4, -10.0, -5.0, -0.5, 0.0, 0.5, 5.0, 7.2, 10.0, 12.0]


@pytest.mark.parametrize("raw", REALISTIC_LOGITS)
def test_sigmoid_maps_realistic_logits_strictly_inside_unit_interval(raw: float) -> None:
    p = sigmoid(raw)
    assert 0.0 < p < 1.0
    assert (p > 0.5) == (raw > 0)  # sign of the logit decides which side of 0.5


def test_sigmoid_is_monotonic_and_symmetric() -> None:
    probs = [sigmoid(x) for x in REALISTIC_LOGITS]
    assert probs == sorted(probs)
    for x in REALISTIC_LOGITS:
        assert sigmoid(x) + sigmoid(-x) == pytest.approx(1.0)


@pytest.mark.parametrize("raw", [-1000.0, -745.0, 709.0, 1000.0])
def test_sigmoid_extreme_logits_stay_bounded_and_finite(raw: float) -> None:
    # In float64, sigmoid saturates to exactly 0.0/1.0 here, so the bound is closed; it must never overflow.
    p = sigmoid(raw)
    assert math.isfinite(p) and 0.0 <= p <= 1.0


def test_reranker_outputs_normalized_scores_not_raw_logits() -> None:
    logits = [-11.4, -3.0, 0.0, 2.0, 7.2]
    result = CrossEncoderReranker(FakeCrossEncoder(logits), threshold=THRESHOLD).rerank("q", candidates(5))
    assert all(0.0 < c.score < 1.0 for c in result.ranked)
    assert result.top_confidence == pytest.approx(sigmoid(7.2))
    assert [c.score for c in result.ranked] == sorted((sigmoid(x) for x in logits), reverse=True)


# ---------- Test 3: threshold gating logic ----------

def test_top_score_042_triggers_low_confidence() -> None:
    logits = [logit(0.42), logit(0.30), logit(0.10)]
    result = CrossEncoderReranker(FakeCrossEncoder(logits), threshold=THRESHOLD).rerank("q", candidates(3))

    assert result.top_confidence == pytest.approx(0.42)
    assert result.status is RerankStatus.LOW_CONFIDENCE
    assert result.needs_fallback
    assert result.chunks == []  # nothing below the cutoff reaches the generator


def test_top_score_081_passes() -> None:
    logits = [logit(0.20), logit(0.81), logit(0.40), logit(0.65)]
    result = CrossEncoderReranker(FakeCrossEncoder(logits), threshold=THRESHOLD, top_k=3).rerank("q", candidates(4))

    assert result.top_confidence == pytest.approx(0.81)
    assert result.status is RerankStatus.OK
    assert not result.needs_fallback
    assert [c.chunk_id for c in result.chunks] == ["d.md#s1", "d.md#s3", "d.md#s2"]


def test_raw_logit_would_have_misrouted_without_sigmoid() -> None:
    # Logit 0.5 is a moderately relevant match: compared raw against 0.55 it would be rejected,
    # but sigmoid(0.5) ~= 0.62 correctly passes. Logit -0.3 -> sigmoid ~= 0.43 correctly fails.
    ok = CrossEncoderReranker(FakeCrossEncoder([0.5]), threshold=THRESHOLD).rerank("q", candidates(1))
    low = CrossEncoderReranker(FakeCrossEncoder([-0.3]), threshold=THRESHOLD).rerank("q", candidates(1))
    assert ok.status is RerankStatus.OK and low.status is RerankStatus.LOW_CONFIDENCE
