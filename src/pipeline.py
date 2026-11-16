"""Orchestrates rewrite -> retrieve (K1) -> re-rank (K2, gate) -> [web fallback] -> generate.

CLI:
    python -m src.pipeline --index                 # build/refresh the vector index from docs/
    python -m src.pipeline "How long is the canary bake time?"
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Mapping, Optional, Sequence

from src.config import Settings
from src.generator import GroundedGenerator
from src.reranker import CrossEncoderReranker
from src.retriever import DocumentRetriever
from src.rewriter import QueryRewriter, cap_query
from src.schemas import PipelineTrace, RAGResponse, RetrievedChunk, Route
from src.web_fallback import WebFallback

logger = logging.getLogger(__name__)

# Bound on the user's question as sent to the generator (the search query has its own, smaller cap).
MAX_QUESTION_CHARS = 2000


class EmptyQueryError(ValueError):
    """Raised for blank input; callers should show a friendly prompt, not an error."""


class RAGPipeline:
    def __init__(
        self,
        rewriter: QueryRewriter,
        retriever: DocumentRetriever,
        reranker: CrossEncoderReranker,
        web_fallback: WebFallback,
        generator: GroundedGenerator,
        k1_candidates: int = 20,
    ) -> None:
        self.rewriter = rewriter
        self.retriever = retriever
        self.reranker = reranker
        self.web_fallback = web_fallback
        self.generator = generator
        self.k1_candidates = k1_candidates

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "RAGPipeline":
        from openai import OpenAI

        settings = settings or Settings.from_env()
        llm = OpenAI(api_key=settings.require_openai_key(), timeout=settings.llm_timeout_s)
        return cls(
            rewriter=QueryRewriter(llm, settings.llm_model, timeout_s=settings.rewrite_timeout_s),
            retriever=DocumentRetriever.from_settings(settings),
            reranker=CrossEncoderReranker.from_pretrained(
                settings.reranker_model, threshold=settings.confidence_threshold, top_k=settings.k2_final
            ),
            web_fallback=WebFallback.from_keys(settings.tavily_api_key, max_results=settings.web_results),
            generator=GroundedGenerator(llm, settings.llm_model),
            k1_candidates=settings.k1_candidates,
        )

    def answer(self, query: str, history: Optional[Sequence[Mapping[str, str]]] = None) -> RAGResponse:
        return self.run(query, history).response

    def run(self, query: str, history: Optional[Sequence[Mapping[str, str]]] = None) -> PipelineTrace:
        """Execute the pipeline and return every intermediate result plus per-stage latency.

        Raises EmptyQueryError for blank input, before any model or network call.
        """
        query = (query or "").strip()
        if not query:
            raise EmptyQueryError("Please enter a question.")
        timings: Dict[str, float] = {}

        with _timed(timings, "rewrite"):
            transformed = self.rewriter.rewrite(query, history) or cap_query(query)
        logger.info("Rewrote query %r -> %r", query, transformed)

        with _timed(timings, "stage1_retrieve"):
            candidates = self.retriever.retrieve(transformed, k=self.k1_candidates)
        with _timed(timings, "stage2_rerank"):
            reranked = self.reranker.rerank(transformed, candidates)

        route: Route
        context: List[RetrievedChunk]
        if reranked.needs_fallback:
            route = "WEB_FALLBACK"
            with _timed(timings, "web_fallback"):
                context = self.web_fallback.search(transformed)
        else:
            route = "INTERNAL_RAG"
            context = reranked.chunks

        with _timed(timings, "generate"):
            # Answer what the user actually asked; the rewrite is only a search aid.
            response = self.generator.generate(
                query=cap_query(query, MAX_QUESTION_CHARS),
                chunks=context,
                route=route,
                confidence=reranked.top_confidence,
                transformed_query=transformed,
            )

        return PipelineTrace(
            original_query=query,
            transformed_query=transformed,
            stage1_candidates=candidates,
            rerank=reranked,
            context_chunks=context,
            response=response,
            timings_ms=timings,
        )


@contextmanager
def _timed(timings: Dict[str, float], name: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = (time.perf_counter() - start) * 1000


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Two-stage RAG pipeline with web fallback")
    parser.add_argument("question", nargs="?", help="Question to answer")
    parser.add_argument("--index", action="store_true", help="(Re)index the docs directory before answering")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()

    if args.index:
        n = DocumentRetriever.from_settings(settings).index_directory(settings.docs_dir)
        print(f"Indexed {n} chunks from {settings.docs_dir}")
    if args.question is not None and not args.question.strip():
        parser.error("question must not be empty")
    if args.question:
        response = RAGPipeline.from_settings(settings).answer(args.question)
        print(json.dumps(response.model_dump(), indent=2))
    elif not args.index:
        parser.print_help()


if __name__ == "__main__":
    main()
