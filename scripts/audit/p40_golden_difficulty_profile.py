"""A.66 WP1-①：评测集**难度分层体检**（只读，不写库）——回答"评测集弱在哪"。

为什么必须先做这个
------------------
A.65 的裁决被"仪器饱和"卡死：136 条 golden 的 hit@5 = **0.9853**，天花板只剩 1.47pp，
所以只能证明"排序必要"，**不能**判定"图谱扩边是否有用"。
根因本轮已查实：golden 的 query 全部取自**对话语料**
（`scripts/t1b_build_qa_golden.py:7-9` 的数据源只有 `data/kb_sources/dialogue_docs.jsonl`，
query 取 content 里的 `患者提问：{title}`），gold = 该对话文档 → **它从来没考过"能不能找到指南知识"**。

本探针给三组结论（不是一堆数字）：
1. **命中位次分层**：gold 落在 top-1 / top-3 / top-10 / 未命中 各多少条（用 `gate2` 的同一把尺子）；
2. **薄弱面清单**：按 `dept` 分层 + 逐条列出未命中题（含 gold 文档是"单块"还是"多块"、
   以及它的 (organ, section_path) 组里有几篇文档 —— 后者直接决定"跨文档邻居"有没有可用空间）；
3. **仪器结论**：现状是否已饱和（hit@10 很高 + 未命中极少）→ 是否必须造难集才能评估扩边。

用法（服务器，需嵌入 8004 + DB）：
    ~/uai/venv/bin/python scripts/audit/p40_golden_difficulty_profile.py \
        --report ~/uai/reports/golden_difficulty_profile.json

退出码：0 报告已出；2 前置缺失（golden 不存在 / 服务不可达 / 全为占位块）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 同一把尺子：doc 级命中判定复用 gate2；占位块检测复用 p39（A.65 踩坑留下的守卫）
from gate2_bge_top10 import doc_level_hit_at_k  # noqa: E402
from p39_graph_neighbor_ranking_sim import count_placeholder_chunks  # noqa: E402

GOLDEN = pathlib.Path.home() / "uai" / "data" / "golden" / "kb_qa_golden_v1.json"

#: 分层标签（顺序即由易到难）
LAYERS = ("top1", "top3", "top10", "miss")


def layer_of(rank: int | None) -> str:
    """命中位次 → 分层标签（`rank` 是 1-based；None = 未命中）。纯函数，便于守卫。"""
    if rank is None or rank <= 0:
        return "miss"
    if rank == 1:
        return "top1"
    if rank <= 3:
        return "top3"
    if rank <= 10:
        return "top10"
    return "miss"


def summarize_layers(rows: list[dict]) -> dict:
    """分层汇总（守恒：四层之和 == 总条数）。纯函数，便于守卫。"""
    counts = dict.fromkeys(LAYERS, 0)
    for r in rows:
        counts[layer_of(r.get("rank"))] += 1
    total = len(rows)
    assert sum(counts.values()) == total, "分层之和必须等于总条数（口径守恒）"
    return {
        "total": total,
        "counts": counts,
        "ratio": {k: round(v / total, 4) if total else 0.0 for k, v in counts.items()},
    }


def instrument_verdict(layers: dict) -> dict:
    """仪器结论：现状评测集是否已饱和（能否用来评估扩边）。

    ⚠️ 必须用**累计** hit@10（top1+top3+top10 三层之和），不是"第 4~10 名"那一层的占比 ——
    首版把单层占比当累计用，136 条里只 1 条落在该层（0.74%）就把结论判成了 `HAS_HEADROOM`。
    纯函数，便于守卫钉住这条教训。
    """
    ratio = layers.get("ratio") or {}
    cum_hit10 = ratio.get("top1", 0.0) + ratio.get("top3", 0.0) + ratio.get("top10", 0.0)
    saturated = cum_hit10 >= 0.95 and ratio.get("miss", 0.0) <= 0.05
    note = (
        "SATURATED：累计 hit@10 ≥ 95% 且未命中 ≤ 5% → 该评测集只能证明'排序必要'，"
        "**无法判定扩边收益**，必须造难集（gold 不在 top-10 的题占多数）才有区分度"
        if saturated
        else "HAS_HEADROOM：仍有未命中题，可直接用本集做扩边对照，但样本量可能不足"
    )
    return {"saturated": saturated, "cumulative_hit_at_10": round(cum_hit10, 4), "note": note}


async def _collect(kb_version: str) -> dict:
    import asyncpg

    from app.config.database import AsyncSessionLocal
    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.rag.protocols import RetrievalRequest
    from app.rag.retriever import Retriever

    if not GOLDEN.is_file():
        raise SystemExit(f"[FATAL] golden 不存在：{GOLDEN}")

    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = [q for q in gold.get("queries", gold) if q.get("query_text")]
    if not queries:
        raise SystemExit("[FATAL] golden 里没有可用 query")

    client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    # ⚠️ 必须传 session_factory：否则命中全是 `_placeholder_chunk`（A.65 踩坑）
    retriever = Retriever(
        vector_client=client,
        embedding_service=emb,
        ultrasound_clip=None,
        bm25_index=None,
        session_factory=AsyncSessionLocal,
    )
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)

    rows: list[dict] = []
    try:
        for i, q in enumerate(queries):
            req = RetrievalRequest(
                query=q["query_text"],
                top_k=10,
                use_query_enhancement=False,
                use_reranking=False,
                use_bm25_fallback=False,
                use_multimodal=False,
            )
            res = await retriever.retrieve(req, enhanced_query=q["query_text"])
            if i == 0:
                ph = count_placeholder_chunks(res.chunks)
                if res.chunks and ph * 2 >= len(res.chunks):
                    raise SystemExit(
                        f"[FATAL] 命中里占位块占多数（{ph}/{len(res.chunks)}）："
                        "session_factory 没生效 → 按来源的统计会静默全 0。先修构造参数。"
                    )

            top_ids = [sc.chunk.chunk_id for sc in res.chunks]
            gold_doc = q.get("relevant_source_doc_id") or ""
            if not top_ids or not gold_doc:
                rows.append(
                    {
                        "query_id": q.get("query_id"),
                        "dept": q.get("dept"),
                        "rank": None,
                        "reason": "无检索结果或缺 gold",
                    }
                )
                continue

            doc_rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id FROM knowledge_chunks"
                " WHERE chunk_id = ANY($1::text[])",
                top_ids,
            )
            doc_by_id = {r["chunk_id"]: (r["source_doc_id"] or "") for r in doc_rows}
            ranking = [(idx, 1.0 - idx * 0.001) for idx in range(len(top_ids))]
            corpus_rows = [{"source_doc_id": doc_by_id.get(cid, "")} for cid in top_ids]

            # 用 gate2 的同一把尺子算"最早命中的位次"（逐个 k 问，取最小的 k）
            rank: int | None = None
            for k in range(1, len(top_ids) + 1):
                if doc_level_hit_at_k(ranking, corpus_rows, gold_doc, k):
                    rank = k
                    break

            # gold 文档的形状：块数 + (organ, section_path) 组内文档数（跨文档邻居的可用空间）
            gold_shape = await conn.fetchrow(
                "SELECT count(*) AS chunks FROM knowledge_chunks"
                " WHERE kb_version = $1 AND source_doc_id = $2",
                kb_version,
                gold_doc,
            )
            group_docs = await conn.fetchval(
                "SELECT count(DISTINCT source_doc_id) FROM knowledge_chunks kc"
                " WHERE kc.kb_version = $1 AND kc.organ = ("
                "   SELECT organ FROM knowledge_chunks WHERE kb_version = $1"
                "   AND source_doc_id = $2 LIMIT 1)"
                " AND COALESCE(kc.section_path,'') = COALESCE(("
                "   SELECT section_path FROM knowledge_chunks WHERE kb_version = $1"
                "   AND source_doc_id = $2 LIMIT 1), '')",
                kb_version,
                gold_doc,
            )
            rows.append(
                {
                    "query_id": q.get("query_id"),
                    "dept": q.get("dept"),
                    "rank": rank,
                    "layer": layer_of(rank),
                    "gold_doc": gold_doc,
                    "gold_doc_kind": gold_doc.split("_")[0] if "_" in gold_doc else gold_doc,
                    "gold_doc_chunks": int(gold_shape["chunks"] or 0) if gold_shape else 0,
                    "gold_group_docs": int(group_docs or 0),
                }
            )
    finally:
        await conn.close()

    layers = summarize_layers(rows)
    by_dept: dict[str, dict] = {}
    for r in rows:
        d = r.get("dept") or "(未知)"
        slot = by_dept.setdefault(d, {"total": 0, "top1": 0, "top3": 0, "top10": 0, "miss": 0})
        slot["total"] += 1
        slot[r.get("layer") or layer_of(r.get("rank"))] += 1
    for slot in by_dept.values():
        slot["hit_at_3"] = round((slot["top1"] + slot["top3"]) / slot["total"], 4)

    weak = sorted(
        [{"dept": d, **v} for d, v in by_dept.items()],
        key=lambda x: (x["hit_at_3"], -x["total"]),
    )[:8]
    missed = [r for r in rows if (r.get("layer") or "miss") == "miss"]

    verdict = instrument_verdict(layers)
    return {
        "probe": "p40_golden_difficulty_profile",
        "kb_version": kb_version,
        "golden": str(GOLDEN),
        "layers": layers,
        "by_dept": by_dept,
        "weakest_depts": weak,
        "missed_queries": missed,
        "gold_doc_shapes": {
            "single_chunk_docs": sum(1 for r in rows if r.get("gold_doc_chunks") == 1),
            "multi_chunk_docs": sum(1 for r in rows if (r.get("gold_doc_chunks") or 0) > 1),
            "gold_with_cross_doc_group": sum(
                1 for r in rows if (r.get("gold_group_docs") or 0) > 1
            ),
            "note": "gold_with_cross_doc_group = gold 所在 (organ, section_path) 组里有 ≥2 篇文档"
            "（决定跨文档邻居有没有可用空间）",
        },
        "instrument": verdict,
        "per_query": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/golden_difficulty_profile.json")
    )
    args = ap.parse_args()

    t0 = time.monotonic()
    try:
        payload = asyncio.run(_collect(args.kb_version))
    except SystemExit as exc:
        print(str(exc))
        return 2
    except Exception as exc:
        print(f"[FATAL] 难度体检失败：{type(exc).__name__}: {exc}")
        return 2

    payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["wall_s"] = round(time.monotonic() - t0, 1)
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lay = payload["layers"]
    print(f"136 条分层：top1 {lay['counts']['top1']} / top3 {lay['counts']['top3']} / "
          f"top10 {lay['counts']['top10']} / miss {lay['counts']['miss']}"
          f"（hit@10 = {lay['ratio']['top10'] + lay['ratio']['top1'] + lay['ratio']['top3']:.4f}）")
    print(f"仪器结论：{payload['instrument']['note'][:80]}...")
    print("最弱科室：" + "，".join(
        f"{w['dept']} hit@3={w['hit_at_3']}（n={w['total']}）" for w in payload["weakest_depts"][:4]
    ))
    print(f"报告：{out}  耗时 {payload['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
