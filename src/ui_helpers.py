"""Framework-agnostic view-model helpers for the web UI (unit-testable without Streamlit)."""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Sequence

from src.sanitize import strip_markdown_images
from src.schemas import PipelineTrace, RetrievedChunk

_CITATION_RE = re.compile(r"\[(\d+)\]")
POPOVER_PREVIEW_CHARS = 450

CITATION_CSS = """
<style>
.cite { position: relative; display: inline-block; cursor: pointer; color: #1f6feb;
        font-weight: 600; font-size: 0.85em; vertical-align: super; outline: none; }
.cite .cite-pop { display: none; position: absolute; z-index: 1000; bottom: 1.6em; left: -1rem;
        width: 26rem; max-width: 80vw; padding: 0.75rem 0.9rem; border-radius: 0.5rem;
        background: #ffffff; color: #1f2328; border: 1px solid #d0d7de;
        box-shadow: 0 8px 24px rgba(0,0,0,0.18); font-weight: 400; font-size: 0.82rem;
        line-height: 1.4; white-space: normal; vertical-align: baseline; }
.cite:hover .cite-pop, .cite:focus .cite-pop, .cite:focus-within .cite-pop { display: block; }
.cite-pop .cite-src { display: block; font-weight: 600; margin-bottom: 0.35rem; word-break: break-all; }
.cite-pop a { color: #1f6feb; }
.meter { position: relative; height: 14px; border-radius: 7px; background: #e6e8eb; margin: 0.35rem 0 0.2rem; }
.meter .fill { height: 100%; border-radius: 7px; }
.meter .cut { position: absolute; top: -4px; width: 2px; height: 22px; background: #1f2328; }
.meter-labels { display: flex; justify-content: space-between; font-size: 0.75rem; opacity: 0.75; }
</style>
"""


def stage1_rows(trace: PipelineTrace) -> List[Dict[str, Any]]:
    """Bi-encoder candidates in retrieval order with distance metrics."""
    return [
        {
            "rank": i,
            "chunk_id": c.chunk_id,
            "cosine_similarity": round(c.score, 4),
            "cosine_distance": round(1.0 - c.score, 4),
            "preview": _preview(c.content, 140),
        }
        for i, c in enumerate(trace.stage1_candidates, start=1)
    ]


def stage2_rows(trace: PipelineTrace, top_n: int = 5) -> List[Dict[str, Any]]:
    """Top-N cross-encoder results with their rank movement relative to stage 1."""
    stage1_rank = {c.chunk_id: i for i, c in enumerate(trace.stage1_candidates, start=1)}
    selected = {c.chunk_id for c in trace.rerank.chunks}
    rows = []
    for new_rank, c in enumerate(trace.rerank.ranked[:top_n], start=1):
        old_rank = stage1_rank.get(c.chunk_id)
        rows.append(
            {
                "rank": new_rank,
                "stage1_rank": old_rank,
                "shift": rank_shift_label(old_rank, new_rank),
                "chunk_id": c.chunk_id,
                "confidence": round(c.score, 4),
                "above_threshold": c.score >= trace.rerank.threshold,
                "sent_to_llm": c.chunk_id in selected,
                "preview": _preview(c.content, 140),
            }
        )
    return rows


def rank_shift_label(old_rank: int | None, new_rank: int) -> str:
    if old_rank is None:
        return "new"
    delta = old_rank - new_rank
    if delta > 0:
        return f"▲ {delta}"
    if delta < 0:
        return f"▼ {-delta}"
    return "–"


def render_answer_html(answer: str, chunks: Sequence[RetrievedChunk], strip_images: bool = False) -> str:
    """Escape the LLM answer and turn valid [n] markers into hover/click popovers.

    The answer is HTML-escaped first, so neither the model nor web snippets can
    inject markup into the page. Markdown survives escaping, so on the web route
    (`strip_images=True`) markdown images are removed too: the browser would
    otherwise fetch an attacker-chosen URL as soon as the answer renders.
    """
    if strip_images:
        answer = strip_markdown_images(answer)
    escaped = html.escape(answer)

    def replace(match: re.Match[str]) -> str:
        n = int(match.group(1))
        if not 1 <= n <= len(chunks):
            return match.group(0)
        return _citation_span(n, chunks[n - 1])

    return _CITATION_RE.sub(replace, escaped)


def source_href(chunk: RetrievedChunk) -> str | None:
    if chunk.source == "web" and chunk.chunk_id.startswith(("http://", "https://")):
        return chunk.chunk_id
    return None


def _citation_span(n: int, chunk: RetrievedChunk) -> str:
    href = source_href(chunk)
    label = html.escape(chunk.chunk_id)
    if href:
        src = f'🌐 <a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a>'
    else:
        src = f"📄 {label}"
    body = html.escape(_preview(chunk.content, POPOVER_PREVIEW_CHARS)).replace("\n", "<br>")
    return (
        f'<span class="cite" tabindex="0">[{n}]'
        f'<span class="cite-pop"><span class="cite-src">{src}</span>{body}</span></span>'
    )


def confidence_meter_html(confidence: float, threshold: float) -> str:
    confidence = min(max(confidence, 0.0), 1.0)
    color = "#2da44e" if confidence >= threshold else "#e8590c"
    return (
        f'<div class="meter"><div class="fill" style="width:{confidence * 100:.1f}%;background:{color}"></div>'
        f'<div class="cut" style="left:calc({threshold * 100:.1f}% - 1px)" title="threshold {threshold:.2f}"></div></div>'
        f'<div class="meter-labels"><span>0.00</span><span>cutoff {threshold:.2f}</span><span>1.00</span></div>'
    )


def _preview(text: str, limit: int) -> str:
    text = re.sub(r"[ \t]+", " ", text.strip())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
