"""Unit tests using fakes, so no network, API keys, or model weights are needed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

import pytest

from src.generator import NO_ANSWER, GroundedGenerator, extract_citations
from src.pipeline import RAGPipeline
from src.reranker import CrossEncoderReranker, sigmoid
from src.retriever import chunk_markdown
from src.rewriter import QueryRewriter
from src.schemas import RerankStatus, RetrievedChunk
from src.web_fallback import DuckDuckGoSearch, TavilySearch, WebFallback

DOCS = Path(__file__).resolve().parent.parent / "docs"


# ---------- fakes ----------

class FakeLLM:
    """Mimics openai.OpenAI().chat.completions.create."""

    def __init__(self, replies: List[str] | None = None, error: Exception | None = None) -> None:
        self.replies = list(replies or [])
        self.error = error
        self.calls: List[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        msg = SimpleNamespace(content=self.replies.pop(0))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeCrossEncoder:
    def __init__(self, logits: List[float]) -> None:
        self.logits = logits

    def predict(self, pairs: List[tuple[str, str]]) -> List[float]:
        assert len(pairs) == len(self.logits)
        return self.logits


class FakeRetriever:
    def __init__(self, chunks: List[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.last_k: int | None = None

    def retrieve(self, query: str, k: int = 20) -> List[RetrievedChunk]:
        self.last_k = k
        return self.chunks[:k]


def chunk(i: int, source: str = "internal_doc") -> RetrievedChunk:
    return RetrievedChunk(chunk_id=f"doc.md#s{i}", content=f"content {i}", score=0.5, source=source)  # type: ignore[arg-type]


# ---------- retriever: chunking ----------

def test_chunking_ignores_hash_lines_inside_code_fences() -> None:
    md = "# Title\n\nIntro.\n\n## Install\n\n```bash\n# macOS / Linux\ncurl x\n```\n\n## Next\n\nMore."
    chunks = chunk_markdown(md, source="guide.md")
    assert [c.chunk_id for c in chunks] == ["guide.md#title", "guide.md#install", "guide.md#next"]
    assert "# macOS / Linux" in chunks[1].text
    assert chunks[1].text.startswith("Title > Install")


def test_chunking_splits_long_sections_with_unique_ids() -> None:
    body = "\n\n".join("word " * 60 for _ in range(10))  # ~3000 chars
    chunks = chunk_markdown(f"# Big\n\n{body}", source="big.md", max_chars=800)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) > 1 and len(set(ids)) == len(ids)
    assert all(len(c.text) <= 800 + len("Big\n\n") for c in chunks)


def test_all_project_docs_chunk_cleanly() -> None:
    files = sorted(DOCS.glob("*.md"))
    assert len(files) >= 5
    for f in files:
        chunks = chunk_markdown(f.read_text(encoding="utf-8"), source=f.name)
        assert chunks, f.name
        assert len({c.chunk_id for c in chunks}) == len(chunks)


# ---------- reranker ----------

def test_sigmoid_is_stable() -> None:
    assert sigmoid(0) == 0.5
    assert sigmoid(1000) == pytest.approx(1.0)
    assert sigmoid(-1000) == pytest.approx(0.0)


def test_reranker_returns_top_k2_when_confident() -> None:
    candidates = [chunk(i) for i in range(20)]
    logits = [-5.0] * 20
    logits[7], logits[3], logits[12] = 4.0, 2.0, 1.0
    result = CrossEncoderReranker(FakeCrossEncoder(logits), threshold=0.55, top_k=3).rerank("q", candidates)

    assert result.status is RerankStatus.OK
    assert [c.chunk_id for c in result.chunks] == ["doc.md#s7", "doc.md#s3", "doc.md#s12"]
    assert result.top_confidence == pytest.approx(sigmoid(4.0))
    assert result.chunks[0].score == pytest.approx(sigmoid(4.0))


def test_reranker_flags_low_confidence_below_threshold() -> None:
    # sigmoid(0.1) ~= 0.525 < 0.55
    result = CrossEncoderReranker(FakeCrossEncoder([0.1, -2.0]), threshold=0.55).rerank("q", [chunk(0), chunk(1)])
    assert result.status is RerankStatus.LOW_CONFIDENCE
    assert result.needs_fallback and result.chunks == []


def test_reranker_threshold_is_inclusive() -> None:
    logit = 0.2
    result = CrossEncoderReranker(FakeCrossEncoder([logit]), threshold=sigmoid(logit)).rerank("q", [chunk(0)])
    assert result.status is RerankStatus.OK


def test_reranker_empty_candidates_is_low_confidence() -> None:
    result = CrossEncoderReranker(FakeCrossEncoder([])).rerank("q", [])
    assert result.needs_fallback and result.top_confidence == 0.0


# ---------- rewriter ----------

def test_rewriter_includes_history_and_cleans_output() -> None:
    llm = FakeLLM(['"Keel prod canary rollout bake time"\nextra line'])
    out = QueryRewriter(llm, "m").rewrite(
        "how long does it take?", history=[{"role": "user", "content": "Tell me about Keel canary rollouts"}]
    )
    assert out == "Keel prod canary rollout bake time"
    assert "Keel canary rollouts" in llm.calls[0]["messages"][1]["content"]


def test_rewriter_falls_back_to_original_on_error() -> None:
    assert QueryRewriter(FakeLLM(error=RuntimeError("boom")), "m").rewrite("  original  ") == "original"


# ---------- web fallback ----------

def test_tavily_results_parsed_into_chunks() -> None:
    client = SimpleNamespace(search=lambda **_: {"results": [
        {"url": "https://a", "title": "A", "content": "alpha", "score": 0.9},
        {"url": "", "title": "bad", "content": "no url"},
        {"url": "https://b", "title": "B", "content": "beta", "score": 0.5},
    ]})
    chunks = TavilySearch("k", client=client).search("q", 3)
    assert [c.chunk_id for c in chunks] == ["https://a", "https://b"]
    assert all(c.source == "web" for c in chunks)


def test_web_fallback_tries_next_provider_on_failure() -> None:
    def broken(**_: Any) -> Any:
        raise ConnectionError("down")

    ddg = SimpleNamespace(text=lambda q, max_results: [{"href": "https://d", "title": "D", "body": "delta"}])
    fallback = WebFallback([TavilySearch("k", client=SimpleNamespace(search=broken)), DuckDuckGoSearch(client=ddg)])
    chunks = fallback.search("q")
    assert [c.chunk_id for c in chunks] == ["https://d"]


# ---------- generator ----------

def test_extract_citations_maps_and_drops_invalid() -> None:
    chunks = [chunk(1), chunk(2)]
    assert extract_citations("A [2]. B [1][2]. C [9].", chunks) == ["[1] doc.md#s1", "[2] doc.md#s2"]


def test_generator_without_context_skips_llm() -> None:
    llm = FakeLLM()
    resp = GroundedGenerator(llm, "m").generate("q", [], "WEB_FALLBACK", 0.2, "q")
    assert resp.answer == NO_ANSWER and resp.citations == [] and llm.calls == []


# ---------- end-to-end routing ----------

def _pipeline(logits: List[float], web_chunks: List[RetrievedChunk], answer: str) -> tuple[RAGPipeline, FakeRetriever]:
    llm = FakeLLM(["rewritten query", answer])
    retriever = FakeRetriever([chunk(i) for i in range(len(logits))])
    web = WebFallback([SimpleNamespace(name="fake", search=lambda q, n: web_chunks)])  # type: ignore[list-item]
    pipeline = RAGPipeline(
        rewriter=QueryRewriter(llm, "m"),
        retriever=retriever,  # type: ignore[arg-type]
        reranker=CrossEncoderReranker(FakeCrossEncoder(logits)),
        web_fallback=web,
        generator=GroundedGenerator(llm, "m"),
    )
    return pipeline, retriever


def test_pipeline_internal_route() -> None:
    logits = [-3.0] * 20
    logits[5] = 3.0
    pipeline, retriever = _pipeline(logits, [], "Bake time is 10 minutes [1].")
    resp = pipeline.answer("hey, how long is bake time?")

    assert retriever.last_k == 20
    assert resp.route_taken == "INTERNAL_RAG"
    assert resp.transformed_query == "rewritten query"
    assert resp.citations == ["[1] doc.md#s5"]
    assert resp.confidence_score == pytest.approx(sigmoid(3.0))


def test_pipeline_web_fallback_route() -> None:
    web = [RetrievedChunk(chunk_id="https://w", content="web fact", score=0.9, source="web")]
    pipeline, _ = _pipeline([-4.0] * 20, web, "Web says so [1].")
    resp = pipeline.answer("what is the weather?")

    assert resp.route_taken == "WEB_FALLBACK"
    assert resp.citations == ["[1] https://w"]
    assert resp.confidence_score < 0.55


def test_duckduckgo_retries_transient_failures() -> None:
    calls: List[int] = []

    def flaky_text(q: str, max_results: int) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("No results found.")  # ddgs raises DDGSException on transient empties
        if len(calls) == 2:
            return []
        return [{"href": "https://d", "title": "D", "body": "delta"}]

    ddg = DuckDuckGoSearch(client=SimpleNamespace(text=flaky_text), attempts=3, backoff_s=0)
    assert [c.chunk_id for c in ddg.search("q", 3)] == ["https://d"] and len(calls) == 3


def test_duckduckgo_gives_up_after_bounded_attempts() -> None:
    def broken(q: str, max_results: int) -> Any:
        raise RuntimeError("down")

    fallback = WebFallback([DuckDuckGoSearch(client=SimpleNamespace(text=broken), attempts=2, backoff_s=0)])
    assert fallback.search("q") == []  # WebFallback swallows the final error and returns no context
