"""Shared fixtures for live QA tests (real OpenAI, ChromaDB index, cross-encoder, web search).

Tests marked `live` run whenever OPENAI_API_KEY is configured and are skipped otherwise.
Deselect them explicitly with:  pytest -m "not live"
"""

from __future__ import annotations

from typing import Any

import pytest

from src.config import Settings


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    settings = Settings.from_env()
    if not settings.openai_api_key:
        pytest.skip("live test: OPENAI_API_KEY is not configured")
    return settings


@pytest.fixture(scope="session")
def openai_client(live_settings: Settings) -> Any:
    from openai import OpenAI

    return OpenAI(api_key=live_settings.openai_api_key, timeout=60)


@pytest.fixture(scope="session")
def live_pipeline(live_settings: Settings) -> Any:
    from src.pipeline import RAGPipeline

    pipeline = RAGPipeline.from_settings(live_settings)
    if pipeline.retriever.count() == 0:
        pipeline.retriever.index_directory(live_settings.docs_dir)
    return pipeline
