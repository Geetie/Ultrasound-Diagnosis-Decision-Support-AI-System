"""A.63 批 WP1 探针：图谱邻居扩展「开 / 关」两态检索指标对比（真机）。

要回答的问题
------------
`settings.graph_query_enabled` 一直是 False。开了到底有没有用？
本探针在**同一进程内**对同一批 golden queries 跑两遍（唯一差异 = 该开关），
因此不受服务重启/缓存/网络抖动影响。

为什么不用 Gate 2（`gate2_bge_top10.py`）当尺子
------------------------------------------------
Gate 2 是「PG 全量语料 + 进程内余弦」，**不经过 `Retriever`**，看不到图谱扩展；
而图谱邻居是 **append 在 top-k 之后**的（`app/rag/retriever.py:247-306`），
所以 top-k 排名指标**按构造不会变**。真正要量的是两件事：

1. **救援（rescue）**：gold 文档不在 baseline top-10 里、却出现在图谱邻居里
   —— 这才是"扩展有没有补上漏掉的知识"；
2. **稀释（dilution）**：加进来的邻居里有多少与 gold 同源（相关的代理指标）；
   不相关邻居越多，进 prompt 的噪声越大。

三态判定（不预设结论）
----------------------
* ``GAIN``：hit@10 / MRR 不回退 **且** rescue > 0 → 值得开；
* ``NO_GAIN``：指标持平、rescue = 0 → 按「不开更简单」处理（如实登记，不硬凑）；
* ``REGRESSION``：hit@10 或 MRR 下降 → 关回去（退出码 1）。

前置缺失显式失败（退出码 2）：golden 缺失 / 嵌入服务不可达 / **库里没有边**
（后者说明还没跑 `scripts/kb_backfill_relations.py`，此时"开了也是空转"）。

退出码：0 = GAIN 或 NO_GAIN；1 = REGRESSION；2 = 前置不满足（可读原因）。

运行（真机）：
    cd ~/uai/src && set -a && source ~/uai/.env && set +a && \
      ~/uai/venv/bin/python scripts/audit/p36_graph_expansion_ab.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

from _audit_common import check, summary  # noqa: E402

GOLDEN = pathlib.Path.home() / "uai" / "data" / "golden" / "kb_qa_golden_v1.json"
REPORT = pathlib.Path.home() / "uai" / "reports" / "graph_expansion_ab.json"
TOP_K = 10
HIT_TOLERANCE = 0.01  # 允许 1pp 抖动（探针自身非零噪声来自检索实现，不是随机性）


def _pg_dsn() -> str:
    from app.config.settings import settings

    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def edge_stats(kb_version: str) -> dict:
    """边投影现状：多少块带 relations、一共多少条边。"""
    import asyncpg

    conn = await asyncpg.connect(_pg_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT
              count(*) AS chunks,
              count(*) FILTER (WHERE jsonb_typeof(metadata->'relations') = 'array') AS with_rel,
              coalesce(sum(
                CASE WHEN jsonb_typeof(metadata->'relations') = 'array'
                     THEN jsonb_array_length(metadata->'relations') ELSE 0 END
              ), 0) AS edges
            FROM knowledge_chunks
            WHERE kb_version = $1
            """,
            kb_version,
        )
    finally:
        await conn.close()
    return {
        "kb_version": kb_version,
        "chunks": int(row["chunks"]),
        "chunks_with_relations": int(row["with_rel"]),
        "edges": int(row["edges"]),
    }


def load_queries(path: pathlib.Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("queries", data) if isinstance(data, dict) else data
    return [
        {
            "query_text": q.get("query_text") or q.get("query") or "",
            "gold": q.get("relevant_source_doc_id") or "",
        }
        for q in items
        if (q.get("relevant_source_doc_id") or q.get("gold"))
    ]


async def run_leg(queries: list[dict], enabled: bool) -> dict:
    """一遍完整跑批；`enabled` 只切换 graph_query_enabled。"""
    from app.config.database import AsyncSessionLocal
    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.rag.protocols import RetrievalMethod, RetrievalRequest
    from app.rag.retriever import Retriever

    client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    retriever = Retriever(
        vector_client=client,
        embedding_service=emb,
        ultrasound_clip=None,
        bm25_index=None,  # dense-only：与 p15 服务腿同构，只让差异来自图谱开关
        session_factory=AsyncSessionLocal,
    )

    prev = bool(getattr(settings, "graph_query_enabled", False))
    settings.graph_query_enabled = enabled
    hits = {1: 0, 5: 0, 10: 0}
    mrr = 0.0
    neighbors_total = 0
    neighbor_docs: list[list[str]] = []
    baseline_docs: list[list[str]] = []
    try:
        for q in queries:
            req = RetrievalRequest(
                query=q["query_text"],
                top_k=TOP_K,
                use_query_enhancement=False,
                use_reranking=False,
                use_bm25_fallback=False,
                use_multimodal=False,
            )
            res = await retriever.retrieve(req, enhanced_query=q["query_text"])
            docs = [sc.chunk.source_doc_id for sc in res.chunks]
            graph_docs = [
                sc.chunk.source_doc_id
                for sc in res.chunks
                if sc.retrieval_source == RetrievalMethod.GRAPH
            ]
            neighbors_total += len(graph_docs)
            baseline_docs.append(docs[:TOP_K])
            neighbor_docs.append(graph_docs)
            gold = q["gold"]
            for k in hits:
                if gold in docs[:k]:
                    hits[k] += 1
            for rank, doc in enumerate(docs, start=1):
                if doc == gold:
                    mrr += 1.0 / rank
                    break
    finally:
        settings.graph_query_enabled = prev
    n = len(queries)
    return {
        "enabled": enabled,
        "n_queries": n,
        "hit_at_1": round(hits[1] / n, 4),
        "hit_at_5": round(hits[5] / n, 4),
        "hit_at_10": round(hits[10] / n, 4),
        "mrr": round(mrr / n, 4),
        "neighbors_total": neighbors_total,
        "neighbors_per_query": round(neighbors_total / n, 3),
        "_baseline_docs": baseline_docs,
        "_neighbor_docs": neighbor_docs,
    }


def compare(queries: list[dict], off: dict, on: dict) -> dict:
    """救援 / 稀释 / 名次变化（把两腿的中间产物对齐到 query 粒度）。"""
    rescue = 0
    rescue_samples: list[dict] = []
    diluted = 0
    relevant_neighbors = 0
    for i, q in enumerate(queries):
        gold = q["gold"]
        base = off["_baseline_docs"][i]
        neigh = on["_neighbor_docs"][i]
        if gold not in base and gold in neigh:
            rescue += 1
            if len(rescue_samples) < 5:
                rescue_samples.append(
                    {
                        "query": q["query_text"][:60],
                        "gold": gold,
                        "neighbor_rank_hint": neigh.index(gold),
                    }
                )
        for doc in neigh:
            if doc == gold:
                relevant_neighbors += 1
            else:
                diluted += 1
    return {
        "rescue_count": rescue,
        "rescue_samples": rescue_samples,
        "neighbors_relevant": relevant_neighbors,
        "neighbors_diluted": diluted,
        "hit_at_10_delta": round(on["hit_at_10"] - off["hit_at_10"], 4),
        "mrr_delta": round(on["mrr"] - off["mrr"], 4),
    }


def verdict_of(off: dict, on: dict, cmp: dict) -> str:
    if (
        on["hit_at_10"] < off["hit_at_10"] - HIT_TOLERANCE
        or on["mrr"] < off["mrr"] - HIT_TOLERANCE
    ):
        return "REGRESSION"
    if on["neighbors_total"] == 0:
        return "NOT_APPLIED"  # 开关开了却一条邻居都没有（空转）
    return "GAIN" if cmp["rescue_count"] > 0 else "NO_GAIN"


async def main() -> int:
    ap = argparse.ArgumentParser(description="图谱邻居扩展开/关两态对比（A.63 WP1）")
    ap.add_argument("--golden", default=str(GOLDEN))
    ap.add_argument("--kb-version", default="")
    ap.add_argument("--limit", type=int, default=0, help=">0 时只跑前 N 条（冒烟）")
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--require-edges", action="store_true",
                    help="库里没有边时直接判前置不满足（默认开启同语义）")
    args = ap.parse_args()

    from app.config.settings import settings

    kb_version = args.kb_version or settings.rag_kb_version
    golden = pathlib.Path(args.golden)
    if not golden.is_file():
        print(f"[FATAL] golden 集不存在：{golden}")
        return 2

    queries = load_queries(golden)
    if args.limit > 0:
        queries = queries[: args.limit]
    if not queries:
        print("[FATAL] golden 集里没有可用条目（缺 query_text / relevant_source_doc_id）")
        return 2

    stats = await edge_stats(kb_version)
    print(f"== 边投影现状（{kb_version}）==")
    print(f"  chunks={stats['chunks']} 带 relations={stats['chunks_with_relations']} "
          f"边数={stats['edges']}", flush=True)
    if stats["edges"] == 0:
        print("[FATAL] 库里没有边 —— 图谱扩展必然是空转。"
              "先跑：scripts/kb_backfill_relations.py --kb-version "
              f"{kb_version}（本探针不替你回填）")
        return 2

    t0 = time.time()
    off = await run_leg(queries, enabled=False)
    t1 = time.time()
    print(f"  关：hit@1={off['hit_at_1']} hit@5={off['hit_at_5']} "
          f"hit@10={off['hit_at_10']} mrr={off['mrr']} [{t1 - t0:.1f}s]", flush=True)
    on = await run_leg(queries, enabled=True)
    t2 = time.time()
    print(f"  开：hit@1={on['hit_at_1']} hit@5={on['hit_at_5']} "
          f"hit@10={on['hit_at_10']} mrr={on['mrr']} "
          f"邻居={on['neighbors_total']} [{t2 - t1:.1f}s]", flush=True)

    cmp = compare(queries, off, on)
    verdict = verdict_of(off, on, cmp)
    print(f"  救援={cmp['rescue_count']} 邻居(相关={cmp['neighbors_relevant']} "
          f"/稀释={cmp['neighbors_diluted']}) hit@10Δ={cmp['hit_at_10_delta']:+} "
          f"MRRΔ={cmp['mrr_delta']:+}")

    check("图谱扩展确实生效（邻居条数 > 0，不是空转）",
          on["neighbors_total"] > 0, f"neighbors={on['neighbors_total']}")
    check("hit@10 不回退（|Δ| ≤ 1pp）",
          cmp["hit_at_10_delta"] >= -HIT_TOLERANCE,
          f"{off['hit_at_10']} -> {on['hit_at_10']}")
    check("MRR 不回退（|Δ| ≤ 1pp）",
          cmp["mrr_delta"] >= -HIT_TOLERANCE, f"{off['mrr']} -> {on['mrr']}")

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "probe": "p36_graph_expansion_ab",
        "kb_version": kb_version,
        "golden": str(golden),
        "top_k": TOP_K,
        "edges": stats,
        "leg_graph_off": {k: v for k, v in off.items() if not k.startswith("_")},
        "leg_graph_on": {k: v for k, v in on.items() if not k.startswith("_")},
        "compare": cmp,
        "verdict": verdict,
        "wall_s": round(t2 - t0, 1),
    }
    out = pathlib.Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  报告：{out}")
    print(f"== 判定：{verdict}")

    code = summary("图谱邻居扩展开/关对比")
    if code == 0 and verdict == "REGRESSION":
        return 1
    return 0 if verdict != "REGRESSION" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
