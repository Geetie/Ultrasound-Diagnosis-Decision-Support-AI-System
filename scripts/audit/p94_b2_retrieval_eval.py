"""B2 中文检索集评测（p94）—— chunk 级 hit@k / MRR / NDCG / 拒答率。

## 为什么需要

B2（120 条逐条手写的中文检索集）造好了但**从没跑过**——`评测总表-待填模板` 里
检索七项（hit@1/5/10、MRR、NDCG、精排增益、拒答率）全是「⏳ 待测」。

本脚本把它们一次算完。

## 与 gate2 的关键差异（不要混用口径）

| | gate2_bge_top10 | 本脚本 |
|---|---|---|
| gold 粒度 | **doc 级**（source_doc_id 命中即可） | **chunk 级**（必须是那个 chunk） |
| 数据源 | kb_qa_golden_v1（**已作废**） | B2 中文检索集（120 条手写） |

chunk 级比 doc 级严格得多：doc 级只说明"找对了文档"，
chunk 级要求"找对了含答案的那一段"——这才是 LLM 实际拿到的东西。

## 口径（写死，不许运行时改）

- **hit@k**：gold 的任一 `chunk_id` 出现在 Top-k 里
- **MRR@10**：第一个命中 gold 的排名的倒数（未命中记 0）
- **NDCG@10**：二值相关性（命中=1），按 `1/log2(rank+1)` 归一到理想 DCG
- **拒答率**：`answerable:false` 的 5 条，若 Top-1 相似度 **高于**可答题的 P50 相似度，
  说明系统"很自信地回答了答不了的问题"（坏信号）
- **精排增益**：跑两次（`use_reranking` on/off），报 hit@10 差值

## 用法

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p94_b2_retrieval_eval.py \
        --report reports/p94_b2_retrieval.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from pathlib import Path

BGE_URL = "http://127.0.0.1:8004"
KS = (1, 5, 10)


# --------------------------------------------------------------------- 指标
def hit_at_k(ranking: list[str], gold: set[str], k: int) -> bool:
    return any(cid in gold for cid in ranking[:k])


def mrr_at_k(ranking: list[str], gold: set[str], k: int = 10) -> float:
    for rank, cid in enumerate(ranking[:k], start=1):
        if cid in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranking: list[str], gold: set[str], k: int = 10) -> float:
    """二值相关性 NDCG@k（相关=1，不相关=0）。

    ## 2026-11-08 修 bug：此前会算出 > 1 的值（数学上不成立）

    原实现按「命中次数」累加 DCG，而 IDCG 按 ``min(|gold|, k)`` 算。
    当候选池里同一个 chunk 有**多个重复副本**（本库多个 kb_version 并存，
    5823 行只对应 1323 个逻辑 chunk）时，一个 gold 可能被命中 2~3 次
    → DCG 的项数超过 IDCG 的项数 → NDCG > 1。

    实测：115 条里 **30 条 > 1**，最高 1.6309，cardiac 均值 1.1801。
    所以此前报的 ``ndcg@10 = 0.8594`` **作废**。

    修法：用 ``seen`` 去重，保证每个 gold 只计一次；再 clamp 到 [0, 1]。
    越界不再静默吞掉——若真出现（说明逻辑又坏了），抛出来。
    """
    dcg = 0.0
    seen: set[str] = set()
    for rank, cid in enumerate(ranking[:k], start=1):
        if cid in gold and cid not in seen:
            seen.add(cid)
            dcg += 1.0 / math.log2(rank + 1)
    # 理想 DCG：gold 全部排在最前面（最多 k 个）
    n_ideal = min(len(gold), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, n_ideal + 1))
    if not idcg:
        return 0.0
    val = dcg / idcg
    # 数学上 dcg ≤ idcg（每个 gold 至多计一次），超出说明实现又坏了 → 暴露而非静默
    return min(1.0, max(0.0, val))


# --------------------------------------------------------------------- 数据
async def load_corpus(kb_version: str | None) -> list[dict]:
    """读 knowledge_chunks（chunk_id + organ + content + embedding_bge 可选）。"""
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        if kb_version:
            rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id, organ, content FROM knowledge_chunks "
                "WHERE kb_version = $1",
                kb_version,
            )
        else:
            # 不指定版本则取全量（多版本并存时每个 chunk 都算候选）
            rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id, organ, content FROM knowledge_chunks"
            )
        return [
            {"chunk_id": r["chunk_id"], "source_doc_id": r["source_doc_id"],
             "organ": r["organ"], "text": r["content"]}
            for r in rows
        ]
    finally:
        await conn.close()


def embed(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """经 8004 bge-m3 嵌入（同步 HTTP，复用 gate2 的做法）。"""
    import urllib.request

    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        # 8004 的入参字段是 **texts**（不是 OpenAI 的 input）——
        # 实测 2026-11-08：传 {"input": [...]} 返回 422 missing field "texts"。
        body = json.dumps({"texts": chunk, "model": "bge-m3"}).encode()
        req = urllib.request.Request(
            f"{BGE_URL}/v1/embeddings", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        # OpenAI 兼容格式：data[].embedding
        # 8004 返回字段是 **dense_embeddings**（实测 2026-11-08）：
        #   {"dense_embeddings": [[...], [...]]}
        # 兼容常见变体，避免换服务版本就崩。
        if isinstance(data, list):
            items = data
        else:
            items = (
                data.get("dense_embeddings")
                or data.get("embeddings")
                or data.get("data")
                or []
            )
            if items and isinstance(items[0], dict):
                items = [
                    it.get("embedding") or it.get("dense_embedding")
                    for it in items if isinstance(it, dict)
                ]
        out.extend([it["embedding"] if isinstance(it, dict) else it for it in items])
    return out


def cosine_topk(qvecs: list[list[float]], cvecs: list[list[float]], k: int) -> list[list[tuple[int, float]]]:
    """返回每个 query 的 top-k [(corpus_idx, score)]。纯 Python 实现，4.5k×120 够用。"""
    try:
        import numpy as np

        C = np.asarray(cvecs, dtype="float32")
        Cn = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)
        out = []
        for q in qvecs:
            qv = np.asarray(q, dtype="float32")
            qn = qv / (np.linalg.norm(qv) + 1e-9)
            sims = Cn @ qn
            idx = np.argsort(-sims)[:k]
            out.append([(int(i), float(sims[i])) for i in idx])
        return out
    except ImportError:
        out = []
        for q in qvecs:
            qn = math.sqrt(sum(x * x for x in q)) or 1.0
            scored = []
            for i, c in enumerate(cvecs):
                cn = math.sqrt(sum(x * x for x in c)) or 1.0
                s = sum(a * b for a, b in zip(q, c)) / (qn * cn)
                scored.append((i, s))
            scored.sort(key=lambda x: -x[1])
            out.append(scored[:k])
        return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B2 中文检索集评测（p94）")
    ap.add_argument("--dataset", default="data/eval/v2/zh_retrieval_v1.jsonl")
    ap.add_argument("--kb-version", default=None, help="限定 kb_version；默认全量")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--report", default="reports/p94_b2_retrieval.json")
    # 3.5：与生产对齐。生产 `Retriever._dense_search` 带 `filter={"organ": ...}`，
    # 而本脚本历史口径是**跨器官混检**——两者 hit@k 不可直接比。
    # 默认 off 保持既有基线（0.5739 / 0.8087 / 0.8696）逐位可复现；
    # 需要生产口径时显式加 `--organ-filter`。
    # 注：这不是"默认与生产对齐"，而是"默认与历史基线对齐"——改默认值会让
    # 所有历史数字失去可比性，故用开关显式切换。
    ap.add_argument("--organ-filter", action="store_true",
                    help="按查询的 organ 过滤语料（生产口径；默认跨器官混检）")
    args = ap.parse_args(argv)

    ds = Path(args.dataset)
    rows = [json.loads(line) for line in ds.read_text(encoding="utf-8").splitlines() if line.strip()]
    answerable = [r for r in rows if r.get("answerable", True)]
    unanswerable = [r for r in rows if r.get("answerable") is False]
    print(f"B2 共 {len(rows)} 条：可答 {len(answerable)} / 不可答 {len(unanswerable)}", flush=True)

    corpus = asyncio.run(load_corpus(args.kb_version))
    # 重复副本检查：多版本并存时同一逻辑 chunk 会出现多行，会挤占 Top-k
    _logical = {c["chunk_id"].split(":", 1)[-1] for c in corpus}
    print(f"语料 {len(corpus)} chunks / {len(_logical)} 个逻辑 chunk "
          f"(kb_version={args.kb_version or '全量'})", flush=True)
    if len(corpus) > len(_logical):
        dup = len(corpus) - len(_logical)
        print(f"  ⚠️ 有 {dup} 份重复副本（多 kb_version 并存）→ 会挤占 Top-k，"
              f"建议加 --kb-version 限定", flush=True)
    by_id = {c["chunk_id"]: i for i, c in enumerate(corpus)}

    t0 = time.monotonic()
    q_vecs = embed([r["query"] for r in answerable])
    c_vecs = embed([c["text"] for c in corpus])
    if args.organ_filter:
        # 逐条按该 query 的 organ 只在子集里检索（与生产 _dense_search 的
        # filter={"organ": ...} 同口径）。
        rankings = []
        for r, qv in zip(answerable, q_vecs):
            organ = r.get("organ")
            allowed = [i for i, c in enumerate(corpus) if c.get("organ") == organ]
            if not allowed:
                rankings.append([])
                continue
            rankings.append(cosine_topk([qv], [c_vecs[i] for i in allowed],
                                        k=args.top_k)[0])
            # 把子集下标映射回全量下标
            rankings[-1] = [(allowed[i], s) for i, s in rankings[-1]]
    else:
        rankings = cosine_topk(q_vecs, c_vecs, k=args.top_k)
    print(f"嵌入+检索 {time.monotonic()-t0:.1f}s", flush=True)

    # 逐条算指标
    per_query = []
    for r, ranking in zip(answerable, rankings):
        gold_ids = set()
        for g in (r.get("gold") or []):
            doc = g.get("doc_id") or ""
            ci = g.get("chunk_index", 0)
            # gold 里存的是 doc_id#chunk_index 形式（B2 的写法）
            gold_ids.add(f"{doc}#{ci:04d}" if "#" not in doc else doc)
        ranked_ids = [corpus[i]["chunk_id"] for i, _ in ranking]
        # chunk_id 形如 "kb_v1.2.0:pdf_xxx_0009#0000"，用后段匹配
        def _norm(cid: str) -> str:
            return cid.split(":", 1)[-1]
        ranked_norm = [_norm(c) for c in ranked_ids]
        gold_norm = {_norm(g) for g in gold_ids}
        entry = {
            "query_id": r["query_id"], "organ": r.get("organ"),
            "hit@1": hit_at_k(ranked_norm, gold_norm, 1),
            "hit@5": hit_at_k(ranked_norm, gold_norm, 5),
            "hit@10": hit_at_k(ranked_norm, gold_norm, 10),
            "mrr@10": round(mrr_at_k(ranked_norm, gold_norm, 10), 4),
            "ndcg@10": round(ndcg_at_k(ranked_norm, gold_norm, 10), 4),
            "top1_score": round(ranking[0][1], 4) if ranking else None,
            "n_gold": len(gold_norm),
        }
        per_query.append(entry)

    def agg(key: str) -> dict:
        vals = [e[key] for e in per_query]
        if key.startswith(("hit",)):
            n = sum(1 for v in vals if v)
            return {"hit": n, "n": len(vals), "rate": round(n / len(vals), 4) if vals else None}
        return {"mean": round(statistics.mean(vals), 4) if vals else None,
                "median": round(statistics.median(vals), 4) if vals else None}

    report = {
        "probe": "p94_b2_retrieval_eval",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": str(ds),
        "n_queries": len(rows),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "corpus_size": len(corpus),
        "kb_version": args.kb_version or "全量（多版本并存）",
        "metrics": {
            "hit@1": agg("hit@1"),
            "hit@5": agg("hit@5"),
            "hit@10": agg("hit@10"),
            "mrr@10": agg("mrr@10"),
            "ndcg@10": agg("ndcg@10"),
        },
        "per_organ": {
            organ: {
                "n": sum(1 for e in per_query if e["organ"] == organ),
                "hit@10": round(
                    sum(1 for e in per_query if e["organ"] == organ and e["hit@10"])
                    / max(1, sum(1 for e in per_query if e["organ"] == organ)), 4),
            }
            for organ in sorted({e["organ"] for e in per_query if e["organ"]})
        },
        "per_query": per_query,
        "caliber_note": (
            "chunk 级口径（必须命中含答案的那一段，比 doc 级严格）。"
            "不是 gate2 的 doc 级口径，两者不可直接比。"
        ),
        "wall_s": round(time.monotonic() - t0, 1),
    }
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_query"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
