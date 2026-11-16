"""Stage 2: cross-encoder re-ranking and confidence gating.

The cross-encoder scores every (query, chunk) pair jointly, which is far more
precise than bi-encoder similarity. Raw logits are mapped to [0, 1] with a
sigmoid; the top value is the pipeline's confidence that the internal docs can
answer the query.
"""

from __future__ import annotations

import logging
import math
from typing import Any, List, Sequence

from src.schemas import RerankResult, RerankStatus, RetrievedChunk

logger = logging.getLogger(__name__)


def sigmoid(x: float) -> float:
    # Numerically stable for large |x|.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class CrossEncoderReranker:
    def __init__(self, model: Any, threshold: float = 0.55, top_k: int = 3) -> None:
        """`model` must expose `predict(pairs, **kwargs) -> sequence of floats` returning raw logits."""
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be within [0, 1]")
        self._model = model
        self._threshold = threshold
        self._top_k = top_k

    @property
    def threshold(self) -> float:
        return self._threshold

    @classmethod
    def from_pretrained(
        cls, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2", threshold: float = 0.55, top_k: int = 3
    ) -> "CrossEncoderReranker":
        from sentence_transformers import CrossEncoder

        return cls(_RawLogitCrossEncoder(CrossEncoder(model_name)), threshold=threshold, top_k=top_k)

    def rerank(self, query: str, candidates: Sequence[RetrievedChunk]) -> RerankResult:
        if not candidates:
            return RerankResult(
                chunks=[], top_confidence=0.0, threshold=self._threshold, status=RerankStatus.LOW_CONFIDENCE
            )

        logits = self._model.predict([(query, c.content) for c in candidates])
        scored = sorted(
            (c.model_copy(update={"score": sigmoid(float(logit))}) for c, logit in zip(candidates, logits)),
            key=lambda c: c.score,
            reverse=True,
        )
        top_confidence = scored[0].score
        status = RerankStatus.OK if top_confidence >= self._threshold else RerankStatus.LOW_CONFIDENCE
        logger.info("Re-ranked %d candidates; top=%.3f status=%s", len(scored), top_confidence, status.value)

        return RerankResult(
            chunks=scored[: self._top_k] if status is RerankStatus.OK else [],
            ranked=scored,
            top_confidence=top_confidence,
            threshold=self._threshold,
            status=status,
        )


class _RawLogitCrossEncoder:
    """Forces raw logits so the sigmoid is applied exactly once, by us.

    sentence-transformers applies a model-config-dependent activation by
    default (Sigmoid for some checkpoints, Identity for ms-marco ones), so we
    pin it to Identity rather than rely on that.
    """

    def __init__(self, model: Any) -> None:
        import torch

        self._model = model
        self._identity = torch.nn.Identity()

    def predict(self, pairs: List[tuple[str, str]]) -> List[float]:
        scores = self._model.predict(pairs, activation_fn=self._identity, convert_to_numpy=True, show_progress_bar=False)
        return [float(s) for s in scores]
