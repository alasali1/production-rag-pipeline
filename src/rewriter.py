"""Stage 0: LLM query rewriting.

Turns conversational or underspecified user input into a compact,
self-contained search query (filler stripped, pronouns resolved from history).

Failure policy: the rewriter is an optimisation, never a dependency.
  * Latency is bounded: one attempt (no retries) with a short timeout.
  * It never raises: timeouts, provider errors, or empty output fall back to
    the (length-capped) original query; blank input returns "" without a call.
  * Exact terms survive: flags, error codes, numbers, versions, identifiers,
    paths and `code spans` from the user's query are re-appended if the LLM
    dropped or altered them.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, List, Mapping, Optional, Sequence

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = """\
You rewrite user questions into optimized search queries for a technical documentation search engine.

Rules:
- Remove conversational filler (greetings, "can you tell me", "I was wondering", thanks).
- Resolve pronouns and vague references ("it", "that", "the second one") using the conversation history.
- Keep every technical term, product name, command, flag, error code, and number exactly as written.
- Expand only unambiguous abbreviations; never invent facts or add terms the user did not imply.
- Output a single line: the rewritten query only. No quotes, no explanation, no trailing punctuation.
"""

MAX_QUERY_CHARS = 512
DEFAULT_TIMEOUT_S = 5.0

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_FLAG_RE = re.compile(r"^--?[A-Za-z][\w-]*(?:=\S+)?$")
_INNER_SEPARATOR_RE = re.compile(r"\w[./:\\]\w")  # paths, dotted names, host:port, URLs
_LEADING_PUNCT = "([{\"'"
_TRAILING_PUNCT = ",;:!?.)]}\"'"


def _is_exact_term(token: str) -> bool:
    """Tokens a search rewrite must never change: retrieval relies on matching them verbatim."""
    return bool(
        _FLAG_RE.match(token)                      # --skip-canary, -n, --region=eu-west-1
        or any(ch.isdigit() for ch in token)       # KEEL-E1042, v2.14.3, 8080, 30s, 502
        or "_" in token.strip("_")                 # KEEL_DEPLOY_TIMEOUT, snake_case
        or (len(token) >= 2 and token.isupper())   # SLA, TLS, ERR
        or _INNER_SEPARATOR_RE.search(token)       # keel.yaml, config/app.toml, db:5432
    )


def protected_terms(query: str) -> List[str]:
    """Return the exact terms in `query` that the rewritten query must contain, in order, de-duplicated."""
    terms = [span.strip() for span in _CODE_SPAN_RE.findall(query) if span.strip()]
    for raw in _CODE_SPAN_RE.sub(" ", query).split():
        token = raw.lstrip(_LEADING_PUNCT).rstrip(_TRAILING_PUNCT)
        if token and _is_exact_term(token):
            terms.append(token)
    return list(dict.fromkeys(terms))


def _contains_term(text: str, term: str) -> bool:
    """Whole-token match, so '10' is not satisfied by '100' or 'v2.1' by 'v2.14'."""
    return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-]|\.\w)", text) is not None


def cap_query(text: str, limit: int = MAX_QUERY_CHARS) -> str:
    """Truncate to `limit` chars, preferring a word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space >= limit // 2 else cut).rstrip()


def enforce_protected_terms(original: str, rewritten: str, limit: int = MAX_QUERY_CHARS) -> str:
    """Append any protected term from `original` that `rewritten` lost, keeping the result within `limit`."""
    missing = [t for t in protected_terms(original) if not _contains_term(rewritten, t)]
    if not missing:
        return cap_query(rewritten, limit)
    logger.warning("Rewriter dropped exact terms %s; re-appending them", missing)
    suffix = " ".join(missing)
    body = cap_query(rewritten, max(limit - len(suffix) - 1, 0))  # terms take priority over rewritten prose
    return cap_query(f"{body} {suffix}", limit)


class QueryRewriter:
    def __init__(
        self,
        client: "OpenAI",
        model: str,
        max_history_turns: int = 6,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        # One attempt only: a retry would multiply the latency budget of an optional step.
        with_options = getattr(client, "with_options", None)
        self._client = with_options(max_retries=0) if callable(with_options) else client
        self._model = model
        self._max_history_turns = max_history_turns
        self._timeout_s = timeout_s

    def rewrite(self, query: str, history: Optional[Sequence[Mapping[str, str]]] = None) -> str:
        """Return an optimized search query (at most MAX_QUERY_CHARS chars). Never raises.

        `history` is a list of {"role": "user"|"assistant", "content": str} turns,
        oldest first. Blank input returns "" without calling the LLM.
        """
        query = (query or "").strip()
        if not query:
            logger.warning("Blank query passed to rewriter; nothing to rewrite")
            return ""
        fallback = enforce_protected_terms(query, cap_query(query))

        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                max_tokens=96,
                timeout=self._timeout_s,
                messages=[
                    {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
                    {"role": "user", "content": self._build_prompt(query, history)},
                ],
            )
            rewritten = (response.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 - timeouts and any provider error fail open
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.warning(
                "Query rewrite failed after %.0f ms (%s: %s); using original query",
                elapsed_ms, type(exc).__name__, exc,
            )
            return fallback

        rewritten = rewritten.splitlines()[0].strip().strip('"').strip() if rewritten else ""
        if not rewritten:
            logger.warning("Rewriter returned empty output; using original query")
            return fallback
        return enforce_protected_terms(query, rewritten)

    def _build_prompt(self, query: str, history: Optional[Sequence[Mapping[str, str]]]) -> str:
        turns = list(history or [])[-self._max_history_turns :]
        if not turns:
            return f"User question:\n{query}"
        transcript = "\n".join(f"{t.get('role', 'user')}: {t.get('content', '')}" for t in turns)
        return f"Conversation history:\n{transcript}\n\nLatest user question:\n{query}"
