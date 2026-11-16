"""Web search fallback used when the re-ranker reports LOW_CONFIDENCE.

Uses Tavily when TAVILY_API_KEY is configured, otherwise DuckDuckGo (no key).
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Mapping, Optional, Protocol

from src.schemas import RetrievedChunk

logger = logging.getLogger(__name__)

MAX_SNIPPET_CHARS = 2000


class WebSearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> List[RetrievedChunk]: ...


def _make_chunk(url: str, title: str, body: str, score: float) -> Optional[RetrievedChunk]:
    url, title, body = (url or "").strip(), (title or "").strip(), (body or "").strip()
    if not url or not body:
        return None
    content = f"{title}\n\n{body}" if title else body
    return RetrievedChunk(chunk_id=url, content=content[:MAX_SNIPPET_CHARS], score=score, source="web")


class TavilySearch:
    name = "tavily"

    def __init__(self, api_key: str, client: Any = None) -> None:
        if client is None:
            from tavily import TavilyClient

            client = TavilyClient(api_key=api_key)
        self._client = client

    def search(self, query: str, max_results: int) -> List[RetrievedChunk]:
        response: Mapping[str, Any] = self._client.search(query=query, max_results=max_results, search_depth="basic")
        chunks = [
            _make_chunk(r.get("url", ""), r.get("title", ""), r.get("content", ""), float(r.get("score") or 0.0))
            for r in response.get("results", [])
        ]
        return [c for c in chunks if c][:max_results]


class DuckDuckGoSearch:
    name = "duckduckgo"

    def __init__(self, client: Any = None, attempts: int = 3, backoff_s: float = 0.5) -> None:
        if client is None:
            from ddgs import DDGS  # successor of the deprecated duckduckgo_search (which now returns no results)

            client = DDGS(timeout=5)
        self._client = client
        self._attempts = max(1, attempts)
        self._backoff_s = backoff_s

    def search(self, query: str, max_results: int) -> List[RetrievedChunk]:
        results = self._text_with_retry(query, max_results)
        # DDG provides no relevance score; use reciprocal rank so order is preserved.
        chunks = [
            _make_chunk(r.get("href", ""), r.get("title", ""), r.get("body", ""), 1.0 / (rank + 1))
            for rank, r in enumerate(results)
        ]
        return [c for c in chunks if c][:max_results]

    def _text_with_retry(self, query: str, max_results: int) -> List[Mapping[str, Any]]:
        """ddgs intermittently fails (~1 in 8 calls measured) and raises on empty results; retry briefly."""
        for attempt in range(1, self._attempts + 1):
            try:
                results = self._client.text(query, max_results=max_results) or []
            except Exception as exc:  # noqa: BLE001 - ddgs raises DDGSException for "No results found."
                if attempt == self._attempts:
                    raise
                logger.warning("DuckDuckGo attempt %d/%d failed (%s); retrying", attempt, self._attempts, exc)
            else:
                if results or attempt == self._attempts:
                    return list(results)
                logger.warning("DuckDuckGo attempt %d/%d returned no results; retrying", attempt, self._attempts)
            time.sleep(self._backoff_s * attempt)
        return []


class WebFallback:
    """Tries providers in order and returns the first non-empty result set."""

    def __init__(self, providers: List[WebSearchProvider], max_results: int = 3) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = providers
        self._max_results = max_results

    @classmethod
    def from_keys(cls, tavily_api_key: Optional[str], max_results: int = 3) -> "WebFallback":
        providers: List[WebSearchProvider] = []
        if tavily_api_key:
            providers.append(TavilySearch(tavily_api_key))
        providers.append(DuckDuckGoSearch())
        return cls(providers, max_results=max_results)

    def search(self, query: str) -> List[RetrievedChunk]:
        for provider in self._providers:
            try:
                chunks = provider.search(query, self._max_results)
            except Exception:  # noqa: BLE001 - network/provider errors: try the next provider
                logger.exception("Web search via %s failed", provider.name)
                continue
            if chunks:
                logger.info("Web fallback via %s returned %d results", provider.name, len(chunks))
                return chunks
            logger.warning("Web search via %s returned no results", provider.name)
        return []
