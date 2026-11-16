"""Runtime settings loaded from environment variables (and .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

EmbeddingBackend = Literal["openai", "local"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Values copied verbatim from .env.example should count as "not configured".
_PLACEHOLDERS = {"", "your_openai_key", "your_tavily_key"}


def _secret(name: str) -> Optional[str]:
    value = os.getenv(name, "").strip()
    return None if value in _PLACEHOLDERS else value


@dataclass(frozen=True)
class Settings:
    openai_api_key: Optional[str]
    tavily_api_key: Optional[str]

    llm_model: str = "gpt-4o-mini"
    embedding_backend: EmbeddingBackend = "openai"
    openai_embedding_model: str = "text-embedding-3-small"
    local_embedding_model: str = "all-MiniLM-L6-v2"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    docs_dir: Path = PROJECT_ROOT / "docs"
    chroma_dir: Path = PROJECT_ROOT / ".chroma"

    k1_candidates: int = 20
    k2_final: int = 3
    confidence_threshold: float = 0.55
    web_results: int = 3

    # The rewriter is optional, so it gets a tight budget (one attempt, no retries).
    rewrite_timeout_s: float = 5.0
    # Per-attempt timeout for answer generation (the OpenAI client default is 600 s).
    llm_timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        backend = os.getenv("EMBEDDING_BACKEND", "openai").strip().lower()
        if backend not in ("openai", "local"):
            raise ValueError(f"EMBEDDING_BACKEND must be 'openai' or 'local', got {backend!r}")
        return cls(
            openai_api_key=_secret("OPENAI_API_KEY"),
            tavily_api_key=_secret("TAVILY_API_KEY"),
            llm_model=os.getenv("LLM_MODEL", cls.llm_model),
            embedding_backend=backend,  # type: ignore[arg-type]
            docs_dir=Path(os.getenv("DOCS_DIR", str(cls.docs_dir))),
            chroma_dir=Path(os.getenv("CHROMA_DIR", str(cls.chroma_dir))),
            confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", cls.confidence_threshold)),
            rewrite_timeout_s=float(os.getenv("REWRITE_TIMEOUT_S", cls.rewrite_timeout_s)),
            llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", cls.llm_timeout_s)),
        )

    def require_openai_key(self) -> str:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.")
        return self.openai_api_key
