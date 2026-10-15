"""A.66 WP1-③：在**自造难集**上复评"图谱扩边 + 邻居排序"——最终裁决（离线，不写库）。

为什么要它（A.65 的局限）
-------------------------
A.65 在原 golden 上判定 `NO_GO_NO_RECALL_GAIN`，但同一份报告里写明：该集 hit@5 已到天花板
0.9853（p40 证实：**129/136 命中 top-1**）→ **测不出扩边收益**。
本探针把测量换成**难集**（`p41` 造的 `hard_query_set_{crossdoc,guideline}.json`，自带
`synthetic: true` / `usage: expansion_ablation_only`），再跑同一套三臂协议。

两段结构
--------
1. **难集成立性前置校验**：逐条算 gold 在现状检索里的位次 → `gold_not_in_top10_ratio`；
   若低于 `--min-hard-ratio`（默认 0.5），直接判 `NO_GO_INSTRUMENT_NOT_READY`
   —— **仪器还不成立，任何结论都不许出**（A.65 的"虚假 GO"教训）。
2. **三臂对照**（与 `p39` 完全同口径）：baseline 现状 top-5 / 任取 5 / 按 query 余弦重排取 5；
   池子 = 现状 top-10 ∪ 跨文档同章节候选（与 `p38 --coverage`、`p39` 同口径）。

预注册判据（**跑之前写死**，与 p39 同源）
-----------------------------------------
* `P0` 扩边必须真带来召回增益：`pool_after >= pool_before + P0_MIN_POOL_GAIN`
* `P1` 排序后不退化：`sorted + P1_TOLERANCE >= baseline`
* `P2` 排序显著优于任取：`sorted >= random + P2_MIN_LIFT`
三态：`GO` / `NO_GO_NO_RECALL_GAIN` / `NO_GO`；另有 `NO_GO_INSTRUMENT_NOT_READY`（前置校验不过）。

用法（服务器，需 8004 + DB）：
    ~/uai/venv/bin/python scripts/audit/p42_reeval_expansion_on_hard_set.py \
        --set crossdoc --report ~/uai/reports/p42_reeval_crossdoc.json

退出码：0 裁决已出（含 NO_GO）；1 前置校验不过（仪器不成立）；2 输入/服务缺失。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 同一把尺子 + 同一套判据（禁止另写一份）
from gate2_bge_top10 import doc_level_hit_at_k  # noqa: E402
from p39_graph_neighbor_ranking_sim import (  # noqa: E402
    P0_MIN_POOL_GAIN,
    P1_TOLERANCE,
    P2_MIN_LIFT,
    TOP_K_FINAL,
    count_placeholder_chunks,
    verdict_of,
)
from t1_eval_retrieval import cosine_topk, embed_texts  # noqa: E402

RANDOM_SAMPLES = 20


async def _run(set_name: str, args) -> tuple[int, dict]:
    import asyncpg

    from app.config.database import AsyncSessionLocal
    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.rag.protocols import RetrievalRequest
    from app.rag.retriever import Retriever

    path = ROOT / "tests" / "reports" / f"hard_query_set_{set_name}.json"
    if not path.is_file():
        raise SystemExit(f"[FATAL] 难集不存在：{path}（先跑 p41_build_hard_query_set.py）")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("synthetic"):
        raise SystemExit("[FATAL] 该文件没有 synthetic 标记 —— 拒绝把它当难集使用")
    queries = data.get("queries") or []
    if not queries:
        raise SystemExit(f"[FATAL] 难集 {set_name} 里没有条目（n=0）")

    client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    retriever = Retriever(
        vector_client=client,
        embedding_service=emb,
        ultrasound_clip=None,
        bm25_index=None,
        session_factory=AsyncSessionLocal,  # ⚠️ 必须给（A.65 占位块踩坑）
    )
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    rnd = random.Random(20261015)
    n = 0
    gold_not_in_top10 = 0
    baseline_hits = random_sum = 0.0
    sorted_hits = 0
    pool_before = pool_after = 0
    cand_total = 0
    rows_detail: list[dict] = []
    try:
        for q in queries:
            req = RetrievalRequest(
                query=q["query_text"],
                top_k=10,
                use_query_enhancement=False,
                use_reranking=False,
                use_bm25_fallback=False,
                use_multimodal=False,
            )
            res = await retriever.retrieve(req, enhanced_query=q["query_text"])
            if n == 0:
                ph = count_placeholder_chunks(res.chunks)
                if res.chunks and ph * 2 >= len(res.chunks):
                    raise SystemExit(
                        f"[FATAL] 占位块占多数（{ph}/{len(res.chunks)}）→ 统计会静默全 0"
                    )
            top_ids = [sc.chunk.chunk_id for sc in res.chunks]
            gold_doc = (q.get("gold_source_doc_id") or "").strip()
            if not top_ids or not gold_doc:
                continue
            doc_rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id FROM knowledge_chunks"
                " WHERE chunk_id = ANY($1::text[])",
                top_ids,
            )
            doc_by_id = {r["chunk_id"]: (r["source_doc_id"] or "") for r in doc_rows}
            ranking = [(idx, 1.0 - idx * 0.001) for idx in range(len(top_ids))]
            corpus_rows = [{"source_doc_id": doc_by_id.get(cid, "")} for cid in top_ids]
            n += 1

            in_top10 = doc_level_hit_at_k(ranking, corpus_rows, gold_doc, len(top_ids))
            if not in_top10:
                gold_not_in_top10 += 1
            # baseline 臂 = 现状 top-5（按检索既有顺序，不重排）
            base_docs = [doc_by_id.get(cid, "") for cid in top_ids[:TOP_K_FINAL]]
            if gold_doc in base_docs:
                baseline_hits += 1
            if gold_doc in {d for d in (doc_by_id.get(cid, "") for cid in top_ids)}:
                pool_before += 1

            # 扩边候选：与种子同 (organ, section_path) 的跨文档块（与 p38/p39 同口径）
            cand_ids: list[str] = []
            seen = set(top_ids)
            for cid in top_ids[:3]:
                row = await conn.fetchrow(
                    "SELECT organ, section_path FROM knowledge_chunks WHERE chunk_id = $1",
                    cid,
                )
                if row is None:
                    continue
                grp = await conn.fetch(
                    "SELECT chunk_id FROM knowledge_chunks"
                    " WHERE kb_version = $1 AND organ = $2"
                    " AND COALESCE(section_path,'') = COALESCE($3,'')",
                    args.kb_version,
                    row["organ"],
                    row["section_path"],
                )
                for g in grp:
                    if g["chunk_id"] not in seen:
                        seen.add(g["chunk_id"])
                        cand_ids.append(g["chunk_id"])
            if len(cand_ids) > args.max_candidates:
                cand_ids = rnd.sample(cand_ids, args.max_candidates)
            cand_total += len(cand_ids)

            pool_ids = list(top_ids) + [c for c in cand_ids if c not in set(top_ids)]
            pool_rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id, content FROM knowledge_chunks"
                " WHERE chunk_id = ANY($1::text[])",
                pool_ids,
            )
            by_id = {r["chunk_id"]: r for r in pool_rows}
            pool = [
                (cid, by_id[cid]["source_doc_id"] or "", by_id[cid]["content"] or "")
                for cid in pool_ids
                if cid in by_id
            ]
            if not pool:
                continue
            if gold_doc in {d for _, d, _ in pool}:
                pool_after += 1

            vecs = embed_texts(
                [q["query_text"], *[t for _, _, t in pool]],
                bge_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}",
            )
            top = cosine_topk([vecs[0]], vecs[1:], min(TOP_K_FINAL, len(vecs) - 1))[0]
            pool_corpus = [{"source_doc_id": d} for _, d, _ in pool]
            if doc_level_hit_at_k(top, pool_corpus, gold_doc, TOP_K_FINAL):
                sorted_hits += 1

            per_q = 0.0
            for _ in range(RANDOM_SAMPLES):
                pick = rnd.sample(range(len(pool)), min(TOP_K_FINAL, len(pool)))
                if gold_doc in {pool[i][1] for i in pick}:
                    per_q += 1.0
            random_sum += per_q / RANDOM_SAMPLES

            if len(rows_detail) < 15:
                rows_detail.append(
                    {
                        "query_id": q.get("query_id"),
                        "gold_in_top10": in_top10,
                        "pool_size": len(pool),
                        "baseline_hit": gold_doc in base_docs,
                    }
                )
    finally:
        await conn.close()

    if not n:
        raise SystemExit("[FATAL] 难集里没有一条能跑通检索")

    hard_ratio = gold_not_in_top10 / n
    ready = hard_ratio >= args.min_hard_ratio
    baseline = baseline_hits / n
    random_hit = random_sum / n
    sorted_hit = sorted_hits / n
    pb, pa = pool_before / n, pool_after / n
    if ready:
        code, why = verdict_of(baseline, random_hit, sorted_hit, pb, pa)
    else:
        code = "NO_GO_INSTRUMENT_NOT_READY"
        why = (
            f"难集成立性不足：gold 不在 top-10 的比例 {hard_ratio:.4f} < {args.min_hard_ratio}"
            " → 仪器仍不成立，任何扩边结论都不许出（A.65 的'虚假 GO'教训）"
        )
    report = {
        "probe": "p42_reeval_expansion_on_hard_set",
        "set_name": set_name,
        "hard_set_file": str(path),
        "synthetic": True,
        "usage": "expansion_ablation_only",
        "kb_version": args.kb_version,
        "n_queries": n,
        "hard_set_ready": {
            "gold_not_in_top10_ratio": round(hard_ratio, 4),
            "threshold": args.min_hard_ratio,
            "ready": ready,
        },
        "hit_at_5": {
            "baseline_no_expansion": round(baseline, 4),
            "random_5_from_pool": round(random_hit, 4),
            "sorted_5_by_cosine": round(sorted_hit, 4),
        },
        "pool_recall": {
            "before": round(pb, 4),
            "after": round(pa, 4),
            "avg_candidates_per_query": round(cand_total / n, 1),
        },
        "preregistered_criteria": {
            "P0_min_pool_gain": P0_MIN_POOL_GAIN,
            "P1_tolerance": P1_TOLERANCE,
            "P2_min_lift": P2_MIN_LIFT,
        },
        "verdict": {"code": code, "why": why},
        "sample_details": rows_detail,
    }
    return (1 if code == "NO_GO_INSTRUMENT_NOT_READY" else 0), report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", choices=("crossdoc", "guideline"), default="crossdoc")
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument("--max-candidates", type=int, default=40)
    ap.add_argument("--min-hard-ratio", type=float, default=0.5)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    if args.report is None:
        args.report = str(pathlib.Path.home() / f"uai/reports/p42_reeval_{args.set}.json")

    t0 = time.monotonic()
    try:
        code, payload = asyncio.run(_run(args.set, args))
    except SystemExit as exc:
        print(str(exc))
        return 2
    except Exception as exc:
        print(f"[FATAL] 复评失败：{type(exc).__name__}: {exc}")
        return 2

    payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["wall_s"] = round(time.monotonic() - t0, 1)
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    h = payload["hit_at_5"]
    print(f"难集 {args.set}：n={payload['n_queries']}｜gold 不在 top-10 的比例 "
          f"{payload['hard_set_ready']['gold_not_in_top10_ratio']}"
          f"（门槛 {payload['hard_set_ready']['threshold']}，ready={payload['hard_set_ready']['ready']}）")
    print(f"hit@5：baseline {h['baseline_no_expansion']} / 任取 {h['random_5_from_pool']} / "
          f"排序 {h['sorted_5_by_cosine']}；池子召回 {payload['pool_recall']['before']} → "
          f"{payload['pool_recall']['after']}")
    print(f"裁决 = **{payload['verdict']['code']}**：{payload['verdict']['why']}")
    print(f"报告：{out}  耗时 {payload['wall_s']}s")
    return code


if __name__ == "__main__":
    sys.exit(main())
