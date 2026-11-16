from __future__ import annotations

from src.reranker import CrossEncoderReranker
from src.schemas import PipelineTrace, RAGResponse, RetrievedChunk
from src.ui_helpers import (
    confidence_meter_html,
    rank_shift_label,
    render_answer_html,
    stage1_rows,
    stage2_rows,
)
from tests.test_pipeline import FakeCrossEncoder, _pipeline, chunk


def _trace() -> PipelineTrace:
    stage1 = [
        RetrievedChunk(chunk_id=f"d.md#s{i}", content=f"c{i}", score=0.9 - i * 0.01, source="internal_doc")
        for i in range(6)
    ]
    logits = [-3.0, 0.0, -1.0, 2.0, -5.0, 1.0]  # stage-2 order: s3, s5, s1, s2, s0, s4
    rerank = CrossEncoderReranker(FakeCrossEncoder(logits), top_k=3).rerank("q", stage1)
    resp = RAGResponse(answer="x", citations=[], route_taken="INTERNAL_RAG",
                       confidence_score=rerank.top_confidence, transformed_query="q")
    return PipelineTrace(original_query="q", transformed_query="q", stage1_candidates=stage1,
                         rerank=rerank, context_chunks=rerank.chunks, response=resp)


def test_stage1_rows_include_distance() -> None:
    rows = stage1_rows(_trace())
    assert rows[0]["rank"] == 1 and rows[0]["cosine_distance"] == round(1 - 0.9, 4)


def test_stage2_rows_show_rank_shift_and_selection() -> None:
    rows = stage2_rows(_trace(), top_n=5)
    assert [r["chunk_id"] for r in rows] == ["d.md#s3", "d.md#s5", "d.md#s1", "d.md#s2", "d.md#s0"]
    assert rows[0]["stage1_rank"] == 4 and rows[0]["shift"] == "▲ 3"
    assert rows[4]["shift"] == "▼ 4"
    assert [r["sent_to_llm"] for r in rows] == [True, True, True, False, False]


def test_rank_shift_labels() -> None:
    assert rank_shift_label(None, 1) == "new"
    assert rank_shift_label(2, 2) == "–"


def test_answer_html_escapes_and_links_citations() -> None:
    web = RetrievedChunk(chunk_id="https://ex.com/a?b=1&c=2", content="<script>x</script> fact",
                         score=0.5, source="web")
    out = render_answer_html("<b>hi</b> fact [1] and bogus [7].", [web])
    assert "<b>hi</b>" not in out and "&lt;b&gt;hi&lt;/b&gt;" in out
    assert "<script>" not in out
    assert 'href="https://ex.com/a?b=1&amp;c=2"' in out
    assert out.count('class="cite"') == 1 and "[7]" in out


def test_meter_clamps() -> None:
    assert "width:100.0%" in confidence_meter_html(1.7, 0.55)


def test_pipeline_run_returns_trace_with_timings() -> None:
    logits = [-3.0] * 20
    logits[5] = 3.0
    pipeline, _ = _pipeline(logits, [], "Answer [1].")
    trace = pipeline.run("q")
    assert len(trace.stage1_candidates) == 20 and len(trace.rerank.ranked) == 20
    assert trace.context_chunks == trace.rerank.chunks
    assert {"rewrite", "stage1_retrieve", "stage2_rerank", "generate"} <= trace.timings_ms.keys()
    assert trace.rerank.threshold == 0.55
