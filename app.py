"""Streamlit UI exposing the two-stage RAG decision engine.

Run from the project root:
    streamlit run app.py
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import streamlit as st

from src.config import Settings
from src.pipeline import RAGPipeline
from src.schemas import PipelineTrace, RetrievedChunk
from src.ui_helpers import (
    CITATION_CSS,
    confidence_meter_html,
    render_answer_html,
    source_href,
    stage1_rows,
    stage2_rows,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="Two-Stage RAG Explorer", page_icon="🔎", layout="wide")
st.markdown(CITATION_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- resources

@st.cache_resource(show_spinner="Loading models and vector index…")
def load_pipeline() -> RAGPipeline:
    settings = Settings.from_env()
    pipeline = RAGPipeline.from_settings(settings)
    if pipeline.retriever.count() == 0:
        pipeline.retriever.index_directory(settings.docs_dir)
    return pipeline


def init_state() -> None:
    st.session_state.setdefault("messages", [])  # [{"role", "content", "trace"?}]
    st.session_state.setdefault("conversational", False)


# ---------------------------------------------------------------- sidebar

def render_sidebar(trace: Optional[PipelineTrace], pipeline: RAGPipeline) -> None:
    with st.sidebar:
        st.header("Pipeline State & Telemetry")

        if trace is None:
            st.info("Submit a query to see how the pipeline routes it.")
        else:
            st.subheader("Transformed query")
            if trace.transformed_query != trace.original_query:
                st.caption(f"Original: {trace.original_query}")
            st.code(trace.transformed_query, language=None, wrap_lines=True)

            st.subheader("Execution path")
            if trace.response.route_taken == "INTERNAL_RAG":
                st.badge("INTERNAL_RAG (High Confidence)", icon="🟢", color="green")
            else:
                st.badge("WEB_FALLBACK (Low Confidence Triggered)", icon="🟠", color="orange")

            st.subheader("Confidence meter")
            conf, cut = trace.rerank.top_confidence, trace.rerank.threshold
            st.metric(
                "Top cross-encoder score",
                f"{conf:.3f}",
                delta=f"{conf - cut:+.3f} vs cutoff {cut:.2f}",
                delta_color="normal",
            )
            st.markdown(confidence_meter_html(conf, cut), unsafe_allow_html=True)

            st.subheader("Stage latency")
            for stage, ms in trace.timings_ms.items():
                st.text(f"{stage:<16} {ms:8.0f} ms")
            st.text(f"{'total':<16} {sum(trace.timings_ms.values()):8.0f} ms")

        st.divider()
        st.subheader("Index")
        st.caption(f"{pipeline.retriever.count()} chunks in vector store")
        if st.button("Rebuild index from docs/", use_container_width=True):
            with st.spinner("Re-indexing…"):
                n = pipeline.retriever.index_directory(Settings.from_env().docs_dir)
            st.success(f"Indexed {n} chunks")
        if st.button("Clear conversation", use_container_width=True):
            st.session_state.messages = []
            st.rerun()


# ---------------------------------------------------------------- main panel

def render_answer(trace: PipelineTrace, key_prefix: str) -> None:
    on_web_route = trace.response.route_taken == "WEB_FALLBACK"
    st.markdown(
        render_answer_html(trace.response.answer, trace.context_chunks, strip_images=on_web_route),
        unsafe_allow_html=True,
    )
    render_source_popovers(trace.context_chunks, trace.response.citations, key_prefix)


def render_source_popovers(chunks: List[RetrievedChunk], citations: List[str], key_prefix: str) -> None:
    """Clickable popover per source (full chunk text or web link). Cited sources are highlighted."""
    if not chunks:
        return
    cited = {c.split(" ", 1)[0] for c in citations}  # "[n]"
    cols = st.columns(len(chunks))
    for i, (col, chunk) in enumerate(zip(cols, chunks), start=1):
        marker = f"[{i}]"
        icon = "🌐" if chunk.source == "web" else "📄"
        with col.popover(
            f"{icon} {marker}",
            type="primary" if marker in cited else "secondary",
            help=chunk.chunk_id,
            use_container_width=True,
            key=f"{key_prefix}-src-{i}",
        ):
            href = source_href(chunk)
            if href:
                st.markdown(f"**Web source:** [{chunk.chunk_id}]({href})")
                st.text(chunk.content)  # untrusted: render as plain text
            else:
                st.markdown(f"**Internal doc:** `{chunk.chunk_id}`")
                st.caption(f"Re-ranker confidence: {chunk.score:.3f}")
                st.markdown(chunk.content)


def render_retrieval_visualizer(trace: PipelineTrace, key_prefix: str) -> None:
    st.markdown("##### Two-stage retrieval")
    with st.expander(f"Stage 1 · Bi-encoder · top {len(trace.stage1_candidates)} candidates", expanded=False):
        st.dataframe(
            stage1_rows(trace),
            hide_index=True,
            use_container_width=True,
            key=f"{key_prefix}-s1",
            column_config={
                "rank": st.column_config.NumberColumn("#", width="small"),
                "chunk_id": st.column_config.TextColumn("Chunk"),
                "cosine_similarity": st.column_config.ProgressColumn(
                    "Cosine sim.", min_value=0.0, max_value=1.0, format="%.3f"
                ),
                "cosine_distance": st.column_config.NumberColumn("Cosine dist.", format="%.4f"),
                "preview": st.column_config.TextColumn("Preview", width="large"),
            },
        )

    rows = stage2_rows(trace, top_n=5)
    with st.expander(f"Stage 2 · Cross-encoder · re-ranked top {len(rows)}", expanded=True):
        st.caption(
            f"Confidence = sigmoid(cross-encoder logit). Cutoff {trace.rerank.threshold:.2f}; "
            f"status **{trace.rerank.status.value}**. ▲/▼ = positions moved vs. stage 1."
        )
        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
            key=f"{key_prefix}-s2",
            column_config={
                "rank": st.column_config.NumberColumn("#", width="small"),
                "stage1_rank": st.column_config.NumberColumn("Stage-1 #", width="small"),
                "shift": st.column_config.TextColumn("Shift", width="small"),
                "chunk_id": st.column_config.TextColumn("Chunk"),
                "confidence": st.column_config.ProgressColumn(
                    "Confidence", min_value=0.0, max_value=1.0, format="%.3f"
                ),
                "above_threshold": st.column_config.CheckboxColumn("≥ cutoff", width="small"),
                "sent_to_llm": st.column_config.CheckboxColumn("Sent to LLM", width="small"),
                "preview": st.column_config.TextColumn("Preview", width="large"),
            },
        )


def render_history() -> None:
    for idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            trace: Optional[PipelineTrace] = msg.get("trace")
            if trace is None:
                st.markdown(msg["content"])
            else:
                render_answer(trace, key_prefix=f"m{idx}")
                render_retrieval_visualizer(trace, key_prefix=f"m{idx}")


def chat_history_for_rewriter() -> List[Dict[str, str]]:
    return [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]


# ---------------------------------------------------------------- app

def main() -> None:
    init_state()
    st.title("🔎 Two-Stage RAG Explorer")
    st.caption("Query rewrite → bi-encoder retrieval (top 20) → cross-encoder re-rank → grounded answer or web fallback")

    try:
        pipeline = load_pipeline()
    except Exception as exc:  # noqa: BLE001 - surface config/model errors in the UI
        st.error(f"Failed to initialise the pipeline: {exc}")
        st.stop()

    mode_col, _ = st.columns([2, 3])
    with mode_col:
        conversational = st.toggle(
            "Conversational follow-ups",
            key="conversational",
            help="On: previous turns are sent to the query rewriter so pronouns/follow-ups are resolved. "
            "Off: every query is a standalone search.",
        )

    query = st.chat_input("Ask about the Keel platform docs…")
    if query is not None and not query.strip():
        st.toast("Please type a question first.", icon="✏️")
        query = None
    if query:
        history = chat_history_for_rewriter() if conversational else None
        if not conversational:
            st.session_state.messages = []
        st.session_state.messages.append({"role": "user", "content": query})
        try:
            with st.spinner("Running pipeline…"):
                trace = pipeline.run(query, history=history)
        except Exception as exc:  # noqa: BLE001
            logging.exception("Pipeline run failed")
            st.session_state.messages.append({"role": "assistant", "content": f"⚠️ Pipeline error: {exc}"})
        else:
            st.session_state.messages.append(
                {"role": "assistant", "content": trace.response.answer, "trace": trace}
            )

    render_history()
    latest: Optional[PipelineTrace] = next(
        (m["trace"] for m in reversed(st.session_state.messages) if m.get("trace") is not None), None
    )
    render_sidebar(latest, pipeline)


main()
