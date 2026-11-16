"""Query-rewriter latency and failure-mode tests (audit item 3)."""

from __future__ import annotations

import re
import socket
import threading
import time
from typing import Any, Iterator, List

import pytest

from src.pipeline import EmptyQueryError
from src.rewriter import (
    MAX_QUERY_CHARS,
    QueryRewriter,
    cap_query,
    enforce_protected_terms,
    protected_terms,
)
from tests.test_pipeline import FakeLLM, _pipeline

# ---------- latency: timeout + no retries ----------


@pytest.fixture
def hanging_server() -> Iterator[str]:
    """A TCP server that accepts connections and never answers (a stalled LLM provider)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen()
    held: List[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                held.append(conn)
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}/v1"
    stop.set()
    thread.join(timeout=1)
    for conn in held:
        conn.close()
    server.close()


def test_rewriter_fails_open_fast_when_provider_hangs(hanging_server: str) -> None:
    from openai import OpenAI

    # Client left at library defaults (600 s timeout, 2 retries) to prove the rewriter overrides them.
    client = OpenAI(api_key="test", base_url=hanging_server)
    rewriter = QueryRewriter(client, "m", timeout_s=0.5)

    started = time.perf_counter()
    out = rewriter.rewrite("hey, what does KEEL-E1042 mean?")
    elapsed = time.perf_counter() - started

    assert out == "hey, what does KEEL-E1042 mean?"
    assert elapsed < 2.0, f"took {elapsed:.1f}s; retries or default timeout still active"


def test_rewriter_passes_timeout_and_disables_retries() -> None:
    class ClientWithOptions(FakeLLM):
        def with_options(self, **kwargs: Any) -> "ClientWithOptions":
            self.options = kwargs
            return self

    llm = ClientWithOptions(["q"])
    QueryRewriter(llm, "m", timeout_s=2.5).rewrite("question")
    assert llm.options == {"max_retries": 0}
    assert llm.calls[0]["timeout"] == 2.5


# ---------- empty input ----------


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_query_returns_empty_without_llm_call(blank: str) -> None:
    llm = FakeLLM()
    assert QueryRewriter(llm, "m").rewrite(blank) == ""
    assert llm.calls == []


def test_pipeline_rejects_blank_query_before_any_stage() -> None:
    pipeline, retriever = _pipeline([0.0], [], "unused")
    with pytest.raises(EmptyQueryError):
        pipeline.run("   ")
    assert retriever.last_k is None  # retrieval never ran


def test_empty_llm_output_falls_back() -> None:
    assert QueryRewriter(FakeLLM(["  \n"]), "m").rewrite("original q") == "original q"


# ---------- exact keyword preservation ----------


def test_protected_terms_extraction() -> None:
    q = ("Why does `keel deploy --force` fail with KEEL-E1042 on v2.14.3 in eu-west-1? "
         "I set KEEL_DEPLOY_TIMEOUT=30s and --skip-canary, port 8080, config/app.toml, re-run didn't help")
    assert protected_terms(q) == [
        "keel deploy --force", "KEEL-E1042", "v2.14.3", "eu-west-1",
        "KEEL_DEPLOY_TIMEOUT=30s", "--skip-canary", "8080", "config/app.toml",
    ]


def test_plain_prose_has_no_protected_terms() -> None:
    assert protected_terms("hey, how long does it take to re-run the canary?") == []


def test_dropped_terms_are_reappended() -> None:
    llm = FakeLLM(["Keel deploy error skip canary flag"])  # LLM mangled the exact tokens
    out = QueryRewriter(llm, "m").rewrite("hi! what is KEEL-E1042 when I pass --skip-canary on v2.14.3?")
    assert out == "Keel deploy error skip canary flag KEEL-E1042 --skip-canary v2.14.3"


def test_present_terms_are_not_duplicated() -> None:
    llm = FakeLLM(["KEEL-E1042 error with --skip-canary"])
    assert QueryRewriter(llm, "m").rewrite("um what's KEEL-E1042 with --skip-canary") == \
        "KEEL-E1042 error with --skip-canary"


def test_term_match_is_whole_token() -> None:
    assert enforce_protected_terms("limit is 10 and version v2.1", "limit 100 version v2.14") == \
        "limit 100 version v2.14 10 v2.1"


# ---------- length caps on every path ----------


def test_fallback_path_is_length_capped() -> None:
    long_q = "word " * 400 + "KEEL-E1042"
    out = QueryRewriter(FakeLLM(error=TimeoutError("slow")), "m").rewrite(long_q)
    assert len(out) <= MAX_QUERY_CHARS
    assert out.endswith("KEEL-E1042")  # the exact term past the cap is kept


def test_rewritten_path_is_capped_but_keeps_terms() -> None:
    out = enforce_protected_terms("check --dry-run", "x " * 600)
    assert len(out) <= MAX_QUERY_CHARS and out.endswith("--dry-run")


def test_cap_prefers_word_boundary() -> None:
    assert cap_query("alpha beta gamma", 12) == "alpha beta"


# ---------- generator gets the user's own question ----------


def test_generator_receives_original_question_and_search_query() -> None:
    logits = [-3.0] * 20
    logits[5] = 3.0
    pipeline, _ = _pipeline(logits, [], "Bake time is 10 minutes [1].")
    pipeline.answer("hey, how long is bake time?")
    prompt = pipeline.generator._client.calls[1]["messages"][1]["content"]
    assert prompt.startswith("<user_question>\nhey, how long is bake time?\n</user_question>")
    assert "<search_query>\nrewritten query\n</search_query>" in prompt
    assert prompt.index("</search_query>") < prompt.index("<sources>")


# ---------- Step 5 QA, Test 1: conversational pronoun resolution (live LLM) ----------


@pytest.mark.live
def test_pronoun_resolved_from_conversation_history(openai_client: Any, live_settings: Any) -> None:
    history = [
        {"role": "user", "content": "Tell me about Kubernetes Worker Nodes."},
        {"role": "assistant", "content": "Kubernetes Worker Nodes are the machines that run your application "
                                         "pods, managed by the kubelet and the container runtime."},
    ]
    out = QueryRewriter(openai_client, live_settings.llm_model).rewrite(
        "What are its system requirements?", history=history
    )

    # Case/plural-insensitive: "Kubernetes worker node system requirements" is a correct rewrite too.
    assert re.search(r"kubernetes worker nodes?", out, re.IGNORECASE), f"pronoun not resolved: {out!r}"
    assert "system requirements" in out.lower(), out
    assert not re.search(r"\bits\b", out, re.IGNORECASE), f"pronoun left in query: {out!r}"
