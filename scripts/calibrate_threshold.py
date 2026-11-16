"""Empirical calibration of the cross-encoder confidence threshold.

Runs a labelled query set through the REAL routing stages (LLM rewriter -> bi-encoder K1=20
-> cross-encoder), records the top sigmoid confidence per query, then sweeps the threshold
and reports both error types:

  false-fallback rate  = answerable in-domain queries whose confidence < t
                         (sent to the web although the docs had the answer)
  false-internal rate  = queries the docs cannot answer whose confidence >= t
                         (kept internal, so no web search happens)

No answers are generated and no web searches are made. Cost: one rewriter call per query.

Run:  .venv/Scripts/python.exe scripts/calibrate_threshold.py
Writes per-query results to scripts/calibration_results.csv.
"""

from __future__ import annotations

import csv
import logging
import math
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.simplefilter("ignore")
logging.disable(logging.WARNING)

from src.config import Settings  # noqa: E402
from src.pipeline import RAGPipeline  # noqa: E402

# label: "in_domain"   - answerable from docs/ (source noted)
#        "near_domain" - sounds like Keel but docs/ do not cover it (verified by grep)
#        "adjacent"    - general DevOps/tech, not about Keel
#        "general"     - unrelated to software
QUERIES: List[Tuple[str, str]] = [
    # --- in-domain (answerable) ---
    ("How long does each canary stage bake in production?", "in_domain"),                       # 03
    ("How long must a release run in staging before it can be promoted to prod?", "in_domain"),  # 03
    ("How many releases does Keel keep for rollbacks?", "in_domain"),                            # 03
    ("When is the weekly deploy freeze?", "in_domain"),                                          # 03
    ("What is the minimum number of replicas for web processes in prod?", "in_domain"),          # 03
    ("What happens if canary p99 latency gets too high during a rollout?", "in_domain"),         # 03
    ("How do I roll back a bad release?", "in_domain"),                                          # 03
    ("How long are CLI session tokens valid?", "in_domain"),                                     # 02/05
    ("hey, how do I pin the keel CLI version in CI?", "in_domain"),                              # 02
    ("What is the maximum size of a secret?", "in_domain"),                                      # 04
    ("After how many days does a secret trigger an age warning?", "in_domain"),                  # 04
    ("How often are addon database credentials rotated?", "in_domain"),                          # 04
    ("What is the API rate limit for service tokens?", "in_domain"),                             # 05
    ("How long are idempotency keys retained?", "in_domain"),                                    # 05
    ("Which webhook events can a project subscribe to?", "in_domain"),                           # 05
    ("How long are prod logs retained?", "in_domain"),                                           # 06
    ("How often are custom metrics scraped?", "in_domain"),                                      # 06
    ("What's the active time series limit per project?", "in_domain"),                           # 06
    ("What is the trace sampling rate in prod?", "in_domain"),                                   # 06
    ("How much error budget does a 99.9% SLO give over 30 days?", "in_domain"),                  # 06
    ("What counts as a SEV1 incident?", "in_domain"),                                            # 07
    ("What does the Incident Commander do during an incident?", "in_domain"),                    # 07
    ("What does error KEEL-2302 mean?", "in_domain"),                                            # 09
    ("my deploy keeps failing with KEEL-2301, what should I check?", "in_domain"),               # 09
    ("How do I fix 'too many connections' errors from PostgreSQL?", "in_domain"),                # 09
    ("How do I do a point-in-time restore of my Postgres database?", "in_domain"),               # 08
    ("Which regions does Keel run in?", "in_domain"),                                            # 01
    ("Which Python versions do the Keel base images support?", "in_domain"),                     # 01
    # --- near-domain (Keel-sounding, NOT in docs) ---
    ("Does Keel have a Terraform provider?", "near_domain"),
    ("How much does Keel cost per service per month?", "near_domain"),
    ("Can I run GPU workloads on Keel?", "near_domain"),
    ("How do I provision a MySQL database on Keel?", "near_domain"),
    ("Does Keel support blue-green deployments?", "near_domain"),
    ("Is there a GraphQL version of the Keel API?", "near_domain"),
    ("Can I attach a Kafka addon to my Keel service?", "near_domain"),
    ("Does Keel provide Helm charts for self-hosting?", "near_domain"),
    ("Who founded the company that built Keel?", "near_domain"),
    ("Does Keel integrate with the Istio service mesh?", "near_domain"),
    # --- adjacent tech (not Keel) ---
    ("How do I configure a Kubernetes HorizontalPodAutoscaler?", "adjacent"),
    ("What is the default port for Redis?", "adjacent"),
    ("How do I write a HEALTHCHECK instruction in a Dockerfile?", "adjacent"),
    ("How do I create a Python virtual environment with venv?", "adjacent"),
    ("What is the difference between TCP and UDP?", "adjacent"),
    ("How do I rebase a git branch onto main?", "adjacent"),
    ("What does HTTP status code 418 mean?", "adjacent"),
    ("How do I write a SQL window function?", "adjacent"),
    # --- general (unrelated) ---
    ("Which country won the 2022 FIFA World Cup?", "general"),
    ("What is the capital of Australia?", "general"),
    ("How do I bake sourdough bread?", "general"),
    ("Who wrote Pride and Prejudice?", "general"),
    ("What's the weather like in Paris in April?", "general"),
    ("How many moons does Jupiter have?", "general"),
    ("What is the best way to learn guitar?", "general"),
    ("Explain photosynthesis simply.", "general"),
]

SWEEP = [0.01, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99]


def main() -> None:
    settings = Settings.from_env()
    pipeline = RAGPipeline.from_settings(settings)
    if pipeline.retriever.count() == 0:
        pipeline.retriever.index_directory(settings.docs_dir)

    with ThreadPoolExecutor(max_workers=8) as pool:  # rewriter calls are I/O-bound
        rewrites = list(pool.map(pipeline.rewriter.rewrite, [q for q, _ in QUERIES]))

    rows: List[Dict[str, object]] = []
    for (query, label), rewritten in zip(QUERIES, rewrites):
        candidates = pipeline.retriever.retrieve(rewritten, k=settings.k1_candidates)
        result = pipeline.reranker.rerank(rewritten, candidates)
        conf = result.top_confidence
        rows.append({
            "label": label, "query": query, "rewritten": rewritten,
            "top_confidence": round(conf, 4),
            "top_logit": round(math.log(conf / (1 - conf)), 2) if 0 < conf < 1 else None,
            "top_chunk": result.ranked[0].chunk_id if result.ranked else "",
        })

    out = ROOT / "scripts" / "calibration_results.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"model={settings.reranker_model}  embeddings={settings.embedding_backend}  "
          f"K1={settings.k1_candidates}  n={len(rows)}\n")
    print(f"{'label':12} {'conf':>7} {'logit':>7}  query")
    for r in sorted(rows, key=lambda r: -float(r["top_confidence"])):  # type: ignore[arg-type]
        print(f"{r['label']:12} {r['top_confidence']:>7.4f} {str(r['top_logit']):>7}  {r['query']}")

    pos = [float(r["top_confidence"]) for r in rows if r["label"] == "in_domain"]  # type: ignore[arg-type]
    neg_all = [float(r["top_confidence"]) for r in rows if r["label"] != "in_domain"]  # type: ignore[arg-type]
    neg_near = [float(r["top_confidence"]) for r in rows if r["label"] == "near_domain"]  # type: ignore[arg-type]
    neg_far = [float(r["top_confidence"]) for r in rows if r["label"] in ("adjacent", "general")]  # type: ignore[arg-type]

    def rate(xs: List[float], pred: bool, t: float) -> str:
        n = sum((x >= t) == pred for x in xs)
        return f"{n}/{len(xs)} ({100 * n / len(xs):.0f}%)"

    print(f"\n{'threshold':>9}  {'false-fallback (in-domain < t)':>31}  {'false-internal: near':>21}  "
          f"{'false-internal: far':>20}  {'false-internal: all':>20}")
    for t in SWEEP:
        print(f"{t:>9.2f}  {rate(pos, False, t):>31}  {rate(neg_near, True, t):>21}  "
              f"{rate(neg_far, True, t):>20}  {rate(neg_all, True, t):>20}")

    print(f"\nin-domain confidence: min={min(pos):.4f} median={sorted(pos)[len(pos)//2]:.4f}")
    print(f"near-domain confidence: max={max(neg_near):.4f}   adjacent+general: max={max(neg_far):.4f}")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
