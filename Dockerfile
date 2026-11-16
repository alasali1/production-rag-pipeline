# syntax=docker/dockerfile:1.7
#
# Two-stage RAG pipeline: Streamlit UI on port 8501.
#
#   docker compose up --build                 # build + run (see compose.yaml)
#   docker build --target test .              # build and run the offline unit tests
#
# Stages: deps -> models -> runtime-base -> {test, runtime (default)}

ARG PYTHON_VERSION=3.11
ARG UV_VERSION=0.13.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------- deps
# Resolve the locked environment into /opt/venv. Only pyproject.toml + uv.lock are
# used, so code edits don't invalidate this (slow) layer.
FROM python:${PYTHON_VERSION}-slim AS deps
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- models
# Bake the cross-encoder weights into the image so the container starts without
# downloading from Hugging Face (and can run with HF_HUB_OFFLINE=1).
FROM deps AS models
ARG RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ENV HF_HOME=/opt/hf
RUN /opt/venv/bin/python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RERANKER_MODEL}')"

# ---------------------------------------------------------------- runtime-base
FROM python:${PYTHON_VERSION}-slim AS runtime-base

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Models are baked in; don't phone home. Set HF_HUB_OFFLINE=0 if EMBEDDING_BACKEND=local
    # (that embedding model is downloaded on first use).
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    ANONYMIZED_TELEMETRY=False \
    # Persistent vector index lives on a volume (see compose.yaml).
    CHROMA_DIR=/data/chroma \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data/chroma \
 && chown -R app:app /data

COPY --from=deps /opt/venv /opt/venv
COPY --from=models --chown=app:app /opt/hf /opt/hf

WORKDIR /app
COPY app.py ./
COPY src ./src
COPY docs ./docs

# ---------------------------------------------------------------- test
# Offline unit tests only (`live` tests need API keys and network).
FROM runtime-base AS test
COPY pyproject.toml ./
COPY tests ./tests
USER app
RUN python -m pytest -m "not live" -q -p no:cacheprovider

# ---------------------------------------------------------------- runtime (default)
FROM runtime-base AS runtime
USER app
VOLUME ["/data/chroma"]
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"]
CMD ["streamlit", "run", "app.py"]
