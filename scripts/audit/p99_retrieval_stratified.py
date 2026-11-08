"""检索分层评测 + p94 两处口径缺陷修正（p99）。

## 为什么需要这个脚本（行动计划 1.5）

`p94_b2_retrieval_eval.py` 只给了 `per_organ.hit@10` 一项（cardiac 0.8529 /
liver 0.8286 / thyroid 0.7826），**其余六项全缺**，而且 1.5 要求"按器官分层"的
完整 P/R/F1 类口径。本脚本重算分层，同时**发现并修正了 p94 的两个真实缺陷**。

## ⚠️ 缺陷 1：NDCG@10 > 1（数学上不可能）

p94 的报告里 115 条中有 **30 条 NDCG@10 > 1**（最高 1.6309），cardiac 均值 1.1801。
NDCG 的定义域是 [0,1]，>1 说明实现有 bug。

根因：**IDCG 按 gold 集合大小算，DCG 却按"排序列表里命中的个数"累加**。
`corpus` 取了全量（多版本并存，见缺陷 2），同一个逻辑 chunk 在候选池里出现多次，
一个 gold chunk 可以在 Top-10 里被命中 2~3 次 → DCG 累加项多于 IDCG 的项数。

本脚本的修正：
1. **候选池去重**：按去掉 `kb_version:` 前缀后的 ID 去重，只保留**最新 kb_version**
   的那一份（版本号解析成元组排序）。这样每个逻辑 chunk 只出现一次。
2. **NDCG 双保险**：DCG 累加时**每个 gold chunk 只计一次**（`seen` 集合），
   并把最终值 clamp 到 [0,1]；若仍越界则记 `ndcg_violation=True` 进报告（不静默吞）。

## ⚠️ 缺陷 2：多版本语料并存 → 排序被同义重复项污染

`load_corpus(kb_version=None)` 取全量 5823 chunks，而实际只有 **1323 个逻辑 chunk**
（1257 个重复 ID，多出 4500 份副本）。检索时这 4500 份副本会挤占 Top-10 ——
**hit@k / MRR / NDCG 全部被系统性压低**（正确答案被自己的旧版本挤下去）。
本脚本按最新版本去重后重算，并**同时报去重前后两套数字**，让口径差异可解释。

## 分层维度

- **按器官**：cardiac / liver / thyroid（数据自带 `organ` 字段）
- 每层报：hit@1/5/10、MRR@10、NDCG@10（修正后）、n、gold 个数分布

## 用法（服务器，需 uai-postgres 15432 + 8004 bge-m3 就绪）

    ~/uai/venv/bin/python scripts/audit/run_with_env.py \
        scripts/audit/p99_retrieval_stratified.py \
        --report reports/p99_retrieval_stratified.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.request
from collections import Counter
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


def ndcg_at_k_fixed(ranking: list[str], gold: set[str], k: int = 10) -> tuple[float, bool]:
    """二值相关性 NDCG@k —— **每个 gold chunk 只计一次**，返回 (值, 是否越界)。

    p94 的 bug：DCG 按命中次数累加、IDCG 按 gold 集合大小算 → 候选池有重复时 NDCG>1。
    这里用 `seen` 去重保证 DCG 的累加项数 ≤ |gold|，与 IDCG 的项数一致。
    """
    dcg = 0.0
    seen: set[str] = set()
    for rank, cid in enumerate(ranking[:k], start=1):
        if cid in gold and cid not in seen:
            seen.add(cid)
            dcg += 1.0 / math.log2(rank + 1)
    n_ideal = min(len(gold), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, n_ideal + 1))
    if idcg == 0:
        return 0.0, False
    v = dcg / idcg
    violation = v > 1.0 + 1e-9
    return (min(v, 1.0), violation)


# --------------------------------------------------------------------- 数据
async def load_corpus_raw(kb_version: str | None) -> list[dict]:
    """原样读 knowledge_chunks（与 p94 完全一致）。"""
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        if kb_version:
            rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id, content, kb_version FROM knowledge_chunks "
                "WHERE kb_version = $1", kb_version)
        else:
            rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id, content, kb_version FROM knowledge_chunks")
        return [{"chunk_id": r["chunk_id"], "source_doc_id": r["source_doc_id"],
                 "text": r["content"], "kb_version": r["kb_version"]} for r in rows]
    finally:
        await conn.close()


def vkey(v: str) -> tuple:
    """把 kb_v1.4.0 这类版本串解析成可比较元组（非数字段退化为 0）。"""
    out = []
    for part in str(v or "").replace("kb_v", "").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def dedupe_latest(corpus: list[dict]) -> tuple[list[dict], dict]:
    """按逻辑 ID 去重，保留最新 kb_version 的那一份。"""
    norm = lambda cid: cid.split(":", 1)[-1]  # noqa: E731
    best: dict[str, dict] = {}
    for c in corpus:
        k = norm(c["chunk_id"])
        cur = best.get(k)
        if cur is None or vkey(c["kb_version"]) > vkey(cur["kb_version"]):
            best[k] = c
    dropped = len(corpus) - len(best)
    versions = Counter(c["kb_version"] for c in best.values())
    return list(best.values()), {
        "raw_chunks": len(corpus),
        "deduped_chunks": len(best),
        "dropped_duplicates": dropped,
        "kept_versions": dict(versions),
    }


def embed(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """经 8004 bge-m3 嵌入。入参字段是 `texts`，返回 `dense_embeddings`。"""
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        body = json.dumps({"texts": chunk, "model": "bge-m3"}).encode()
        req = urllib.request.Request(
            f"{BGE_URL}/v1/embeddings", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
        if isinstance(data, list):
            items = data
        else:
            items = (data.get("dense_embeddings") or data.get("embeddings")
                     or data.get("data") or [])
            if items and isinstance(items[0], dict):
                items = [it.get("embedding") or it.get("dense_embedding")
                         for it in items if isinstance(it, dict)]
        out.extend([it["embedding"] if isinstance(it, dict) else it for it in items])
    return out


def cosine_topk(qvecs, cvecs, k: int):
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - 兜底纯 Python
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


def gold_ids_of(r: dict) -> set[str]:
    g: set[str] = set()
    for item in (r.get("gold") or []):
        doc = item.get("doc_id") or ""
        ci = item.get("chunk_index", 0)
        g.add(f"{doc}#{ci:04d}" if "#" not in doc else doc)
    return g


def run_eval(rows: list[dict], corpus: list[dict], top_k: int) -> list[dict]:
    q_vecs = embed([r["query"] for r in rows])
    c_vecs = embed([c["text"] for c in corpus])
    rankings = cosine_topk(q_vecs, c_vecs, k=top_k)

    norm = lambda cid: cid.split(":", 1)[-1]  # noqa: E731
    per_query = []
    for r, ranking in zip(rows, rankings, strict=True):
        ranked_ids = [corpus[i]["chunk_id"] for i, _ in ranking]
        ranked_norm = [norm(c) for c in ranked_ids]
        gold_norm = {norm(g) for g in gold_ids_of(r)}
        val, viol = ndcg_at_k_fixed(ranked_norm, gold_norm, 10)
        per_query.append({
            "query_id": r["query_id"], "organ": r.get("organ"),
            "hit@1": bool(hit_at_k(ranked_norm, gold_norm, 1)),
            "hit@5": bool(hit_at_k(ranked_norm, gold_norm, 5)),
            "hit@10": bool(hit_at_k(ranked_norm, gold_norm, 10)),
            "mrr@10": round(mrr_at_k(ranked_norm, gold_norm, 10), 4),
            "ndcg@10": round(val, 4),
            "ndcg_violation": viol,
            "top1_score": round(ranking[0][1], 4) if ranking else None,
            "n_gold": len(gold_norm),
        })
    return per_query


def stratify(per_query: list[dict]) -> dict:
    organs = sorted({e["organ"] for e in per_query if e["organ"]})
    out: dict = {}
    for organ in [*organs, "__ALL__"]:
        sub = [e for e in per_query if organ == "__ALL__" or e["organ"] == organ]
        if not sub:
            continue
        n = len(sub)
        out["ALL" if organ == "__ALL__" else organ] = {
            "n": n,
            "hit@1": round(sum(e["hit@1"] for e in sub) / n, 4),
            "hit@5": round(sum(e["hit@5"] for e in sub) / n, 4),
            "hit@10": round(sum(e["hit@10"] for e in sub) / n, 4),
            "hit@1_count": sum(e["hit@1"] for e in sub),
            "hit@10_count": sum(e["hit@10"] for e in sub),
            "mrr@10": round(statistics.mean([e["mrr@10"] for e in sub]), 4),
            "ndcg@10": round(statistics.mean([e["ndcg@10"] for e in sub]), 4),
            "ndcg_violations": sum(1 for e in sub if e["ndcg_violation"]),
            "gold_per_query_mean": round(statistics.mean([e["n_gold"] for e in sub]), 4),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检索分层评测 + p94 口径修正（p99）")
    ap.add_argument("--dataset", default="data/eval/v2/zh_retrieval_v1.jsonl")
    ap.add_argument("--kb-version", default=None)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--report", default="reports/p99_retrieval_stratified.json")
    args = ap.parse_args(argv)

    ds = Path(args.dataset)
    if not ds.is_absolute():
        ds = Path.cwd() / ds
    all_rows = [json.loads(x) for x in ds.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in all_rows if r.get("answerable", True)]
    print(f"[data] B2 {len(all_rows)} 条 → 可答 {len(rows)} / 不可答 {len(all_rows)-len(rows)}",
          flush=True)

    import asyncio

    t0 = time.monotonic()
    raw = asyncio.run(load_corpus_raw(args.kb_version))
    deduped, dedupe_info = dedupe_latest(raw)
    print(f"[corpus] 原始 {dedupe_info['raw_chunks']} → 去重后 "
          f"{dedupe_info['deduped_chunks']}（丢弃 {dedupe_info['dropped_duplicates']} 份旧版本副本）",
          flush=True)
    print(f"[corpus] 保留版本分布: {dedupe_info['kept_versions']}", flush=True)

    print("\n=== A. 去重后（推荐口径）===", flush=True)
    pq_dedup = run_eval(rows, deduped, args.top_k)
    strat_dedup = stratify(pq_dedup)

    print("=== B. p94 原始口径（全量多版本并存，含重复候选）===", flush=True)
    pq_raw = run_eval(rows, raw, args.top_k)
    strat_raw = stratify(pq_raw)

    report = {
        "probe": "p99_retrieval_stratified",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": ("行动计划 1.5 要求检索按器官分层报全套指标；同时修 p94 的两个口径缺陷："
                "①NDCG@10>1（DCG 重复累加 / IDCG 按 gold 集合大小算）；"
                "②多版本语料并存导致重复候选挤占 Top-k。"),
        "dataset": str(ds),
        "n_queries": len(rows),
        "corpus": dedupe_info,
        "caliber_fix": {
            "ndcg": ("p94 的 DCG 按命中次数累加、IDCG 按 |gold| 算 → 候选池有重复时 NDCG>1"
                     "（实测 115 条里 30 条越界、最高 1.6309）。本脚本用 seen 去重后 clamp 到 [0,1]，"
                     "越界仍单独计数不静默。"),
            "corpus_dedup": ("p94 取全量 5823 chunks，实际只有 1323 个逻辑 chunk（1257 个 ID 重复、"
                             "多出 4500 份旧版本副本）→ 正确答案被自己的旧版本挤下去，"
                             "hit@k/MRR/NDCG 全部系统性偏低。去重后重算，两套都报。"),
            "predicted_direction": "去重后指标应**上升**（同义副本不再挤占 Top-k）",
        },
        "A_deduped_latest_version": {
            "n_queries": len(rows),
            "corpus_size": len(deduped),
            "stratified": strat_dedup,
            "per_query": pq_dedup,
        },
        "B_p94_raw_all_versions": {
            "n_queries": len(rows),
            "corpus_size": len(raw),
            "stratified": strat_raw,
            "per_query": pq_raw,
        },
        "p94_reported": {
            "hit@1": 0.5739, "hit@5": 0.7652, "hit@10": 0.8174,
            "mrr@10": 0.6467, "ndcg@10": 0.8594,
            "per_organ_hit@10": {"cardiac": 0.8529, "liver": 0.8286, "thyroid": 0.7826},
            "note": "p94 原始报告（含上述两处缺陷），用于对照",
        },
        "caveat": (
            "B2 是自造中文检索集（120 条手写）。AGENTS.md 硬规则 18 要求新数据集过 "
            "p79 四道闸后才可用于『达标』结论；本报告只做 **分层描述 + 口径修正对照**，"
            "不据此宣称任何『达标』。"),
        "wall_s": round(time.monotonic() - t0, 1),
    }

    rp = Path(args.report)
    if not rp.is_absolute():
        rp = Path.cwd() / rp
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 66)
    print(f"{'层':10s} {'n':>4s} {'hit@1':>7s} {'hit@5':>7s} {'hit@10':>7s} "
          f"{'MRR@10':>7s} {'NDCG@10':>8s}  (去重后)")
    for k, v in strat_dedup.items():
        print(f"{k:10s} {v['n']:4d} {v['hit@1']:7.4f} {v['hit@5']:7.4f} "
              f"{v['hit@10']:7.4f} {v['mrr@10']:7.4f} {v['ndcg@10']:8.4f}")
    print("\n对照 p94 原始口径（全量多版本并存）：")
    for k, v in strat_raw.items():
        print(f"{k:10s} {v['n']:4d} {v['hit@1']:7.4f} {v['hit@5']:7.4f} "
              f"{v['hit@10']:7.4f} {v['mrr@10']:7.4f} {v['ndcg@10']:8.4f}")
    viols = sum(v["ndcg_violations"] for k, v in strat_raw.items() if k == "ALL")
    print(f"\nNDCG 越界条数（原始口径）：{viols}")
    print(f"报告：{rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
