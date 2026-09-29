"""T1c：检索调优网格——加权 RRF（Elastic 官方公式）+ 两阶段 rerank top-N。

方法论（不造轮子）：
- 加权 RRF：Elastic 官方博客 weighted-reciprocal-rank-fusion-rrf（2025-09-15）
  fused(d) = Σ weight_i × 1/(rank_i + rank_constant)，rank 从 1 起。
  本脚本沿用 t1_eval_retrieval.rrf_fuse 的 0-based 实现（+1 等价于 1-based）。
- 两阶段检索：召回 Top-N 候选 → Cross-Encoder（8005 bge-reranker）精排 →
  取 Top-k（业界惯例：召回 50/100 → 精排）。
- 相关性口径：source_doc 级（QA 型 golden 的 gold = 来源文档全部 chunk），
  chunk 变体重建后同一 golden 仍可用。

用法（真机 ~/uai/src，需 8004/8005 就绪）：
    PYTHONPATH=~/uai/src python scripts/t1c_eval_retrieval.py \
        --golden-set data/golden/kb_qa_golden_v1.json \
        --output reports/t1_tuning_report.json
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

from t1_eval_retrieval import (
    bm25_topk,
    cosine_topk,
    embed_texts,
)

_WEIGHT_GRID = [(0.5, 0.5), (0.6, 0.4), (0.7, 0.3), (0.4, 0.6)]
_K_CONST_GRID = [10, 60, 100]
_CANDIDATE_DEPTH = 100  # dense/bm25 各取 Top-100 召回池（与既有基线一致）
_K_VALUES = [5, 10]


def weighted_rrf_fuse(
    rank_a: list[list[int]],
    rank_b: list[list[int]],
    w_a: float,
    w_b: float,
    top_k: int,
    k_const: int,
) -> list[list[int]]:
    """加权 RRF：score = w_a/(k+rank_a+1) + w_b/(k+rank_b+1)（0-based rank）。

    单腿命中文档只加该腿的份（与 Elastic 加权 RRF 语义一致）。
    """
    fused: list[list[int]] = []
    for ids_a, ids_b in zip(rank_a, rank_b, strict=True):
        scores: dict[int, float] = {}
        for rank, idx in enumerate(ids_a):
            scores[idx] = scores.get(idx, 0.0) + w_a / (k_const + rank + 1)
        for rank, idx in enumerate(ids_b):
            scores[idx] = scores.get(idx, 0.0) + w_b / (k_const + rank + 1)
        ordered = sorted(scores, key=lambda i: scores[i], reverse=True)
        fused.append(ordered[:top_k])
    return fused


def _http_post_json(url: str, body: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def doc_level_metrics(
    rankings: list[list[int]],
    corpus: list[dict],
    queries: list[dict],
    k_values: list[int],
) -> dict:
    """source_doc 级指标：hit = Top-K 内出现 gold source_doc 的任一 chunk。"""
    per_q: dict[int, int] = {}
    for qi, ranking in enumerate(rankings):
        gold_src = queries[qi]["relevant_source_doc_id"]
        hit_rank = 0
        for rank, idx in enumerate(ranking, start=1):
            if corpus[idx]["source_doc_id"] == gold_src:
                hit_rank = rank
                break
        per_q[qi] = hit_rank
    out: dict[str, float] = {}
    for k in k_values:
        hits = sum(1 for r in per_q.values() if 0 < r <= k)
        out[f"recall@{k}"] = hits / len(per_q)
    rr = [1.0 / r for r in per_q.values() if r > 0]
    out["mrr@10"] = sum(rr) / len(per_q)
    # 单 gold 文档时 nDCG@10 = log2(2)/log2(rank+1)
    ndcg = [
        1.0 / _log2(r + 1) if 0 < r <= 10 else 0.0 for r in per_q.values()
    ]
    out["ndcg@10"] = sum(ndcg) / len(per_q)
    return out


def _log2(x: float) -> float:
    import math

    return math.log2(x)


def _load_corpus_from_db(kb_version: str) -> list[dict]:
    """chunk 变体实验：直接从 PG 读取指定 kb_version 的 corpus。"""
    import asyncio

    import asyncpg

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings

    async def _load() -> list[dict]:
        conn = await asyncpg.connect(to_asyncpg_dsn(settings.database_url))
        try:
            rows = await conn.fetch(
                """
                SELECT chunk_id, source_doc_id, content
                FROM knowledge_chunks WHERE kb_version = $1 ORDER BY chunk_id
                """,
                kb_version,
            )
        finally:
            await conn.close()
        return [
            {
                "chunk_id": r["chunk_id"],
                "source_doc_id": r["source_doc_id"],
                "text": r["content"],
            }
            for r in rows
        ]

    return asyncio.run(_load())


def main() -> int:
    parser = argparse.ArgumentParser(description="检索调优网格（QA golden）")
    parser.add_argument("--golden-set", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--bge-url", default="http://127.0.0.1:8004")
    parser.add_argument("--reranker-url", default="http://127.0.0.1:8005")
    parser.add_argument("--rerank-topn", default="20,50,100")
    parser.add_argument("--skip-rerank", action="store_true")
    parser.add_argument(
        "--db-corpus-kb-version",
        default=None,
        help="覆盖 corpus 来源：从 PG 读取该 kb_version 的 chunks（chunk 变体实验用）；"
        "golden 的 source_doc 级相关性使跨变体评测仍然有效",
    )
    args = parser.parse_args()

    golden = json.loads(Path(args.golden_set).read_text(encoding="utf-8"))
    if args.db_corpus_kb_version:
        corpus: list[dict] = _load_corpus_from_db(args.db_corpus_kb_version)
    else:
        corpus = golden["corpus"]
    queries: list[dict] = golden["queries"]
    print(f"corpus={len(corpus)} queries={len(queries)}")

    corpus_texts = [c["text"] for c in corpus]
    query_texts = [q["query_text"] for q in queries]

    print("[1/3] 嵌入 corpus + queries（8004 bge-m3）...")
    corpus_vecs = embed_texts(corpus_texts, args.bge_url)
    query_vecs = embed_texts(query_texts, args.bge_url)
    dense_rank = [
        [idx for idx, _ in hits]
        for hits in cosine_topk(query_vecs, corpus_vecs, _CANDIDATE_DEPTH)
    ]

    print("[2/3] BM25 Top-100（jieba）...")
    bm25_pairs = bm25_topk(query_texts, corpus_texts, _CANDIDATE_DEPTH)
    bm25_rank = [[idx for idx, _s in hits] for hits in bm25_pairs]

    report: dict = {
        "golden_set": args.golden_set,
        "kb_version": golden.get("kb_version"),
        "corpus_size": len(corpus),
        "num_queries": len(queries),
        "relevance_level": "source_doc",
        "methodology": {
            "weighted_rrf": "Elastic 官方：fused=Σ w_i/(rank_i+rank_constant)，rank 1-based（实现用 0-based+1 等价）",
            "two_stage_rerank": "召回 Top-N → 8005 bge-reranker → Top-10",
        },
        "baseline_rrf_k60": {},
        "weighted_rrf_grid": [],
        "rerank_experiments": [],
    }

    # ① 既有基线：未加权 RRF k=60（与 t1_eval_retrieval 同口径）。
    # 类型防御：上游 cosine_topk/bm25_topk 可能返回 bare int 或 (idx, score)
    # 对（不同版本行为不一），统一归一化为 bare int 后再显式配对。
    from t1_eval_retrieval import rrf_fuse

    def _bare(rank: list) -> list[int]:
        return [i if isinstance(i, int) else i[0] for i in rank]

    dense_rank = [_bare(r) for r in dense_rank]
    bm25_rank = [_bare(r) for r in bm25_rank]
    dense_pairs = [[(i, 1.0) for i in r] for r in dense_rank]
    bm25_pairs = [[(i, 1.0) for i in r] for r in bm25_rank]
    baseline = [
        [i for i, _s in ranking]
        for ranking in rrf_fuse(dense_pairs, bm25_pairs, _CANDIDATE_DEPTH, k_const=60)
    ]
    m = doc_level_metrics(baseline, corpus, queries, _K_VALUES)
    report["baseline_rrf_k60"] = m
    print(f"基线 RRF(k=60, 等权): {json.dumps(m, ensure_ascii=False)}")

    # ② 加权 RRF 网格（Elastic 公式）
    print("[3/3] 加权 RRF 网格...")
    best = (None, -1.0, None)
    for w_dense, w_sparse in _WEIGHT_GRID:
        for k_const in _K_CONST_GRID:
            fused = weighted_rrf_fuse(
                dense_rank, bm25_rank, w_dense, w_sparse, _CANDIDATE_DEPTH, k_const
            )
            m = doc_level_metrics(fused, corpus, queries, _K_VALUES)
            report["weighted_rrf_grid"].append(
                {
                    "weights": {"dense": w_dense, "sparse": w_sparse},
                    "rank_constant": k_const,
                    "metrics": m,
                }
            )
            score = m["recall@10"] + m["ndcg@10"]
            if score > best[1]:
                best = ((w_dense, w_sparse, k_const), score, m)
            print(
                f"  w=({w_dense},{w_sparse}) k={k_const}: "
                f"R@10={m['recall@10']:.4f} nDCG={m['ndcg@10']:.4f}"
            )
    report["best_weighted_rrf"] = {
        "weights": {"dense": best[0][0], "sparse": best[0][1]},
        "rank_constant": best[0][2],
        "metrics": best[2],
    }

    # ③ 两阶段 rerank（用最优加权 RRF 的融合排名做召回池）
    if not args.skip_rerank:
        bw = report["best_weighted_rrf"]
        pool_rank = weighted_rrf_fuse(
            dense_rank,
            bm25_rank,
            bw["weights"]["dense"],
            bw["weights"]["sparse"],
            _CANDIDATE_DEPTH,
            bw["rank_constant"],
        )
        for top_n in [int(x) for x in args.rerank_topn.split(",")]:
            t0 = time.perf_counter()
            reranked: list[list[int]] = []
            for qi, ranking in enumerate(pool_rank):
                cand = ranking[:top_n]
                body = {
                    "query": query_texts[qi],
                    "documents": [corpus_texts[i] for i in cand],
                    "top_k": min(10, len(cand)),
                }
                resp = _http_post_json(
                    f"{args.reranker_url}/v1/rerank", body, timeout=300
                )
                ordered = [cand[r["index"]] for r in resp["results"]]
                reranked.append(ordered)
            elapsed = time.perf_counter() - t0
            m = doc_level_metrics(reranked, corpus, queries, _K_VALUES)
            report["rerank_experiments"].append(
                {
                    "rerank_top_n": top_n,
                    "final_top_k": 10,
                    "total_latency_s": round(elapsed, 1),
                    "avg_latency_ms_per_query": round(elapsed * 1000 / len(queries), 1),
                    "metrics": m,
                }
            )
            print(
                f"  rerank top_n={top_n}: R@10={m['recall@10']:.4f} "
                f"nDCG={m['ndcg@10']:.4f} ({elapsed:.0f}s total)"
            )

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"报告写出: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
