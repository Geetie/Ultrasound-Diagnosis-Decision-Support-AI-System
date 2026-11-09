"""B2 中文检索集评测 v2（p94B）—— chunk 级 hit@k / MRR / NDCG，带四组可 A/B 的开关。

## 为什么另起一个脚本，而不是改 p94

`scripts/audit/p94_b2_retrieval_eval.py` 已经是**基线口径的唯一来源**
（`reports/p94_organ_filtered.json` 里的 0.5739 / 0.8087 / 0.8696 / 0.6744 就是它算的）。
在它上面改开关，等于让"基线"和"实验"共用一个会变的文件——复现不了。

本脚本只做**追加**：同样的 chunk 级口径、同样的 gold 解析，加上四个正交开关，
每次只开一个，用来回答「检索改造行动计划 第 3 组」的四个问题。

## 与生产（`app/rag/retriever.py`）的对齐

| 维度 | 生产 | 本脚本 |
|---|---|---|
| dense 腿 | `_dense_search`：pgvector 余弦 + `filter={"organ": ...}` | 同（`--organ-filter` 开） |
| sparse 腿 | `BM25Index.search`：`app.rag.bm25_fallback.tokenize` + BM25Okapi | **同一个** `tokenize` 函数，不另写 |
| 融合 | `_rrf_fuse`：`score = Σ w_leg / (k + rank)`，等权 | 同，可网格搜 `w_dense:w_bm25` |
| organ 过滤 | 有（`request.organ`） | `--organ-filter` 开关，**默认开**（与生产对齐） |
| 精排 | `BgeRerankerService` → 8005 `/v1/rerank` | 同（`--rerank` 开） |

**注意**：p94 原脚本是**跨器官混检且只有 dense 腿**，与生产分布不同；
本脚本默认 `--organ-filter` + `--fusion`，才是生产口径。两者数字**不可直接比**，
报告里必须注明。

## 四个开关

| 开关 | 对应任务 | 默认 |
|---|---|---|
| `--query-expansion` | 3.2 查询扩展（`app.rag.query_expansion.expand_query`） | off |
| `--fusion` + `--w-dense/--w-bm25` | 3.3 BM25 vs dense 融合权重 | off（= 纯 dense，p94 口径） |
| `--rerank` + `--rerank-depth` | 3.4 精排 A/B | off |
| `--organ-filter` | 3.5 与生产对齐 | **on** |

## 用法

    # 基线（= p94 口径：纯 dense、跨器官）
    ~/uai/venv/bin/python scripts/audit/run_with_env.py \\
        scripts/audit/p94b_retrieval_ab.py --report reports/p94b_base.json

    # 生产口径基线（dense+BM25 RRF 等权 + organ 过滤）
    ... --fusion --report reports/p94b_prod.json

    # 查询扩展 on
    ... --fusion --query-expansion --report reports/p94b_qe.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import urllib.request
from pathlib import Path

BGE_URL = "http://127.0.0.1:8004"
RERANK_URL = "http://127.0.0.1:8005"
# 与 RetrievalRequest.top_k 默认一致（app/rag/protocols.py:117）。
DEFAULT_TOP_K = 20


# ------------------------------------------------------------------- 指标
def hit_at_k(ranking: list[str], gold: set[str], k: int) -> bool:
    return any(cid in gold for cid in ranking[:k])


def mrr_at_k(ranking: list[str], gold: set[str], k: int = 10) -> float:
    for rank, cid in enumerate(ranking[:k], start=1):
        if cid in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranking: list[str], gold: set[str], k: int = 10) -> float:
    """二值相关性 NDCG@k（相关=1，不相关=0）。

    ## 2026-11-09 修 bug（与 p94 同样的错，此处漏修）

    原实现按「命中次数」累加 DCG，而 IDCG 按 ``min(|gold|, k)`` 算。
    当候选池里同一 chunk 有**多个版本副本**（本库多 kb_version 并存）时，
    一个 gold 会被命中 2~3 次 → DCG 项数 > IDCG 项数 → **NDCG > 1**。

    实测：同一 gold 出现 3 次 → 2.1309；出现 2 次排 rank2 → 1.1309（正确 0.5）。
    随机 5000 例中 **2392 例越界**。

    **危害**：本脚本是 ``reports/p94b_*.json`` 12 份已发布 A/B 报告的产出者。
    那 12 份报告里越界是 0 条，**只是因为 kb 限定到 kb_v1.4.0 后没有重复副本
    —— 是数据运气，不是代码正确**。一旦语料又变多版本并存，同样的 bug 立刻复现。

    修法：``seen`` 去重 + clamp [0,1]。
    """
    dcg = 0.0
    seen: set[str] = set()
    for rank, cid in enumerate(ranking[:k], start=1):
        if cid in gold and cid not in seen:
            seen.add(cid)
            dcg += 1.0 / math.log2(rank + 1)
    n_ideal = min(len(gold), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, n_ideal + 1))
    if not idcg:
        return 0.0
    return min(1.0, max(0.0, dcg / idcg))


# ------------------------------------------------------------------- HTTP
def _post_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """经 8004 bge-m3 嵌入。

    8004 的入参字段是 **texts**（不是 OpenAI 的 `input`），返回是
    `dense_embeddings`（实测 2026-11-08；传 `input` 返回 422 missing field "texts"）。
    """
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        data = _post_json(
            f"{BGE_URL}/v1/embeddings",
            {"texts": chunk, "model": "bge-m3"},
            timeout=180,
        )
        items = (
            data.get("dense_embeddings")
            or data.get("embeddings")
            or data.get("data")
            or []
        )
        if items and isinstance(items[0], dict):
            items = [
                it.get("embedding") or it.get("dense_embedding")
                for it in items
                if isinstance(it, dict)
            ]
        out.extend(items)
    return out


def rerank_scores(query: str, documents: list[str]) -> list[float]:
    """经 8005 `/v1/rerank` 打分。返回**原始 logit**（与生产 `BgeRerankerService` 一致）。

    服务的返回是 `{"results": [{"index": i, "relevance_score": s}, ...]}`，
    `index` 是 documents 里的**原始下标**；且服务端已按分数降序排过 ——
    所以不能按返回顺序当排名用，必须按 `index` 回填。
    """
    if not documents:
        return []
    data = _post_json(
        f"{RERANK_URL}/v1/rerank",
        {"query": query, "documents": documents, "top_k": 0},
        timeout=180,
    )
    scores = [0.0] * len(documents)
    for item in data.get("results") or []:
        idx = int(item["index"])
        if 0 <= idx < len(scores):
            scores[idx] = float(item["relevance_score"])
    return scores


# ------------------------------------------------------------------- 语料
async def load_corpus(kb_version: str | None) -> list[dict]:
    """读 knowledge_chunks（chunk_id / organ / content / 库内 embedding）。

    用库里的 `embedding` 而不是现算：两者实测余弦 **0.999999**（2026-11-08 抽样 5 条），
    而库内的正是生产 pgvector 里灌的那一份，口径最一致，也省掉 1297 次现算。
    """
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        sql = (
            "SELECT chunk_id, organ, content, embedding::text AS emb "
            "FROM knowledge_chunks WHERE embedding IS NOT NULL"
        )
        if kb_version:
            rows = await conn.fetch(sql + " AND kb_version = $1", kb_version)
        else:
            rows = await conn.fetch(sql)
    finally:
        await conn.close()
    return [
        {
            "chunk_id": r["chunk_id"],
            "organ": r["organ"],
            "text": r["content"],
            "embedding": json.loads(r["emb"]),
        }
        for r in rows
    ]


# ------------------------------------------------------------------- 检索腿
def cosine_topk(
    qvec: list[float], cvecs: list[list[float]], k: int, allowed: list[int]
) -> list[tuple[int, float]]:
    """单条 query 的 dense top-k（只在与 `allowed` 的交集里取）。"""
    import numpy as np

    C = np.asarray(cvecs, dtype="float32")
    q = np.asarray(qvec, dtype="float32")
    qn = q / (np.linalg.norm(q) + 1e-9)
    if len(allowed) == len(cvecs):
        Cn = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)
        sims = Cn @ qn
    else:
        sub = C[allowed]
        sub = sub / (np.linalg.norm(sub, axis=1, keepdims=True) + 1e-9)
        sims = sub @ qn
    order = np.argsort(-sims)[:k]
    return [(allowed[int(i)], float(sims[int(i)])) for i in order]


def rrf_fuse(
    legs: dict[str, list[tuple[int, float]]],
    weights: dict[str, float],
    rrf_k: int = 60,
) -> list[tuple[int, float]]:
    """与 `Retriever._rrf_fuse` 同构：score(d) = Σ w_leg / (rrf_k + rank_leg(d))。

    生产的 `_rrf_fuse` 是等权（每个腿各 1/(k+rank)）；本函数把每个腿的贡献
    乘以 `weights[leg]`，`weights` 全 1 时与生产**逐位等价**。
    """
    fused: dict[int, float] = {}
    for leg, hits in legs.items():
        w = weights.get(leg, 1.0)
        for rank, (idx, _score) in enumerate(hits, start=1):
            fused[idx] = fused.get(idx, 0.0) + w / (rrf_k + rank)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)


# ------------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B2 中文检索集 A/B 评测（p94B）")
    ap.add_argument("--dataset", default="data/eval/v2/zh_retrieval_v1.jsonl")
    ap.add_argument("--kb-version", default=None, help="限定 kb_version；默认全量")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                    help=f"最终返回条数（生产默认 {DEFAULT_TOP_K}）")
    ap.add_argument("--report", default="reports/p94b_retrieval_ab.json")
    ap.add_argument("--label", default="", help="报告里的实验标签，如 qe_on")
    # --- 3.5 organ 过滤（默认与生产对齐 = 开） ---
    ap.add_argument("--organ-filter", dest="organ_filter", action="store_true",
                    default=True, help="按查询的 organ 过滤语料（生产口径，默认开）")
    ap.add_argument("--no-organ-filter", dest="organ_filter", action="store_false",
                    help="跨器官混检（= p94 原口径）")
    # --- 3.2 查询扩展 ---
    ap.add_argument("--query-expansion", action="store_true",
                    help="开启中文缩写/同义词扩展（app.rag.query_expansion）")
    # --- 3.3 融合权重 ---
    ap.add_argument("--fusion", action="store_true",
                    help="开 BM25 稀疏腿并与 dense 做 RRF（关=纯 dense，p94 口径）")
    ap.add_argument("--w-dense", type=float, default=1.0)
    ap.add_argument("--w-bm25", type=float, default=1.0)
    ap.add_argument("--rrf-k", type=int, default=60, help="RRF 常数（生产 60）")
    # --- 3.4 精排 ---
    ap.add_argument("--rerank", action="store_true", help="召回后接 8005 精排")
    ap.add_argument("--rerank-depth", type=int, default=20,
                    help="精排前召回多少条候选（生产 top_k 默认 20）")
    args = ap.parse_args(argv)

    from app.rag.query_expansion import expand_query

    qe = (lambda t: expand_query(t, enabled=True)) if args.query_expansion else (
        lambda t: t
    )

    ds = Path(args.dataset)
    rows = [
        json.loads(line)
        for line in ds.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    answerable = [r for r in rows if r.get("answerable", True)]
    unanswerable = [r for r in rows if r.get("answerable") is False]
    organ_counts: dict[str, int] = {}
    for r in answerable:
        organ_counts[r.get("organ") or "?"] = organ_counts.get(r.get("organ") or "?", 0) + 1
    print(
        f"B2 共 {len(rows)} 条：可答 {len(answerable)} / 不可答 {len(unanswerable)}"
        f" | 分器官 {organ_counts}",
        flush=True,
    )

    corpus = asyncio.run(load_corpus(args.kb_version))
    print(f"语料 {len(corpus)} chunks (kb_version={args.kb_version or '全量'})", flush=True)
    if not corpus:
        print("[FATAL] 语料为空", file=sys.stderr)
        return 2

    from app.rag.bm25_fallback import tokenize  # noqa: F401  确保依赖可用

    t_start = time.monotonic()
    per_query = []

    from rank_bm25 import BM25Okapi

    cvecs = [c["embedding"] for c in corpus]
    orgs = [c["organ"] for c in corpus]
    bm25 = None
    if args.fusion:
        t0 = time.monotonic()
        bm25 = BM25Okapi([tokenize(c["text"]) for c in corpus])
        print(f"BM25 索引构建 {time.monotonic() - t0:.1f}s", flush=True)

    def _allowed(organ: str | None) -> list[int]:
        if args.organ_filter and organ:
            return [i for i, o in enumerate(orgs) if o == organ]
        return list(range(len(corpus)))

    depth = max(args.top_k, args.rerank_depth if args.rerank else 0)
    t_embed = 0.0
    t_rerank = 0.0

    for r in answerable:
        allowed = _allowed(r.get("organ"))
        qtext = qe(r["query"])

        t0 = time.monotonic()
        qvec = embed([qtext])[0]
        dense_hits = cosine_topk(qvec, cvecs, depth, allowed)
        t_embed += time.monotonic() - t0

        if args.fusion:
            # 稀疏腿：与生产 `BM25Index.search` 同口径——只在该 query 的 organ
            # 子集里打分（organ 过滤对所有腿生效，不只是 dense 腿）。
            scores = bm25.get_scores(tokenize(qtext))
            sparse_all = [(i, max(0.0, float(scores[i]))) for i in allowed]
            sparse_all.sort(key=lambda kv: kv[1], reverse=True)
            fused = rrf_fuse(
                {"dense": dense_hits, "sparse": sparse_all[:depth]},
                {"dense": args.w_dense, "sparse": args.w_bm25},
                rrf_k=args.rrf_k,
            )
        else:
            fused = dense_hits

        if args.rerank:
            cand = fused[: args.rerank_depth]
            t0 = time.monotonic()
            scores = rerank_scores(qtext, [corpus[i]["text"] for i, _ in cand])
            t_rerank += time.monotonic() - t0
            ranked = sorted(
                ((cand[j][0], scores[j]) for j in range(len(cand))),
                key=lambda kv: kv[1],
                reverse=True,
            )[: args.top_k]
        else:
            ranked = fused[: args.top_k]

        gold_norm = set()
        for g in r.get("gold") or []:
            doc = g.get("doc_id") or ""
            ci = g.get("chunk_index", 0)
            gold_norm.add(f"{doc}#{ci:04d}" if "#" not in doc else doc.split(":", 1)[-1])

        ranked_norm = [corpus[i]["chunk_id"].split(":", 1)[-1] for i, _ in ranked]
        per_query.append(
            {
                "query_id": r["query_id"],
                "organ": r.get("organ"),
                "hit@1": hit_at_k(ranked_norm, gold_norm, 1),
                "hit@5": hit_at_k(ranked_norm, gold_norm, 5),
                "hit@10": hit_at_k(ranked_norm, gold_norm, 10),
                "mrr@10": round(mrr_at_k(ranked_norm, gold_norm, 10), 4),
                "ndcg@10": round(ndcg_at_k(ranked_norm, gold_norm, 10), 4),
                "top1_score": round(ranked[0][1], 4) if ranked else None,
                "n_gold": len(gold_norm),
                "n_candidates": len(allowed),
            }
        )

    def agg(key: str) -> dict:
        vals = [e[key] for e in per_query]
        if key.startswith("hit"):
            n = sum(1 for v in vals if v)
            return {"hit": n, "n": len(vals),
                    "rate": round(n / len(vals), 4) if vals else None}
        return {
            "mean": round(statistics.mean(vals), 4) if vals else None,
            "median": round(statistics.median(vals), 4) if vals else None,
        }

    per_organ = {}
    for organ in sorted({e["organ"] for e in per_query if e["organ"]}):
        sub = [e for e in per_query if e["organ"] == organ]
        per_organ[organ] = {
            "n": len(sub),
            "hit@1": round(sum(1 for e in sub if e["hit@1"]) / len(sub), 4),
            "hit@5": round(sum(1 for e in sub if e["hit@5"]) / len(sub), 4),
            "hit@10": round(sum(1 for e in sub if e["hit@10"]) / len(sub), 4),
            "mrr@10": round(statistics.mean([e["mrr@10"] for e in sub]), 4),
        }

    report = {
        "probe": "p94b_retrieval_ab",
        "label": args.label,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": str(ds),
        "n_queries": len(rows),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "n_per_organ": organ_counts,
        "corpus_size": len(corpus),
        "kb_version": args.kb_version or "全量（多版本并存）",
        "switch": {
            "organ_filter": args.organ_filter,
            "query_expansion": args.query_expansion,
            "fusion": args.fusion,
            "w_dense": args.w_dense,
            "w_bm25": args.w_bm25,
            "rrf_k": args.rrf_k,
            "rerank": args.rerank,
            "rerank_depth": args.rerank_depth,
            "top_k": args.top_k,
        },
        "metrics": {
            "hit@1": agg("hit@1"),
            "hit@5": agg("hit@5"),
            "hit@10": agg("hit@10"),
            "mrr@10": agg("mrr@10"),
            "ndcg@10": agg("ndcg@10"),
        },
        "per_organ": per_organ,
        "per_query": per_query,
        "timing": {
            "embed_s": round(t_embed, 1),
            "rerank_s": round(t_rerank, 1),
            "wall_s": round(time.monotonic() - t_start, 1),
        },
        "caliber_note": (
            "chunk 级口径（必须命中含答案的那一段，比 doc 级严格）。"
            "gold 取 dataset 里的 doc_id#chunk_index，chunk_id 用 ':' 后段归一。"
        ),
    }
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("per_query",)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
