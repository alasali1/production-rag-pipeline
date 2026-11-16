"""Data models shared across pipeline stages."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field

ChunkSource = Literal["internal_doc", "web"]
Route = Literal["INTERNAL_RAG", "WEB_FALLBACK"]


class RetrievedChunk(BaseModel):
    """A unit of context passed between retrieval, re-ranking, and generation."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(..., description="Stable identifier: '<file>#<section>' for docs, URL for web.")
    content: str
    score: float = Field(..., description="Stage-specific relevance score (similarity, confidence, or provider score).")
    source: ChunkSource


class RAGResponse(BaseModel):
    """Final pipeline output."""

    answer: str
    citations: List[str] = Field(
        default_factory=list,
        description="Cited sources as '[n] <chunk_id>', where n matches the inline marker in the answer.",
    )
    route_taken: Route
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Re-ranker confidence of the best internal chunk.")
    transformed_query: str


class RerankStatus(str, Enum):
    OK = "OK"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


class RerankResult(BaseModel):
    """Output of the stage-2 re-ranker."""

    chunks: List[RetrievedChunk] = Field(..., description="Top-K2 chunks; scores are sigmoid confidences.")
    ranked: List[RetrievedChunk] = Field(
        default_factory=list, description="All candidates re-scored and sorted best-first (for telemetry)."
    )
    top_confidence: float = Field(..., ge=0.0, le=1.0)
    threshold: float = Field(0.55, ge=0.0, le=1.0)
    status: RerankStatus

    @property
    def needs_fallback(self) -> bool:
        return self.status is RerankStatus.LOW_CONFIDENCE


class PipelineTrace(BaseModel):
    """Full record of one pipeline run, for UIs and debugging."""

    original_query: str
    transformed_query: str
    stage1_candidates: List[RetrievedChunk] = Field(..., description="Bi-encoder results; score = cosine similarity.")
    rerank: RerankResult
    context_chunks: List[RetrievedChunk] = Field(..., description="Chunks passed to the generator, in citation order.")
    response: RAGResponse
    timings_ms: Dict[str, float] = Field(default_factory=dict)
