"""A.64 WP2：知识库**结构体检**（只读 SQL）——用数据决定"要不要改切分"。

为什么先跑这个（A.63 的教训）
----------------------------
A.63 给图谱扩展判了 `NOT_APPLIED`，理由是**适用面 0/136**：136 条 golden 查询的 top-3 种子块
**没有一个带边**，因为边只在"同一文档的相邻块"之间写（`builder._attach_relations`），
而命中的是"一篇一块"的对话语料 —— 单块文档**结构上不可能有邻居**。

在动手改切分（overlap / semantic / 标题结构）之前，必须先回答：
**到底是"块切得太粗/太细"，还是"命中根本不在长文档上"** —— 两者对应的修法完全不同，
搞错方向就是白干一轮。

本探针给三组数字（全部可从 DB 与既有报告算出，不需要新建库）：
1. **文档粒度**：每 `source_doc_id` 的块数分布（=1 / 2-3 / 4+）→ "一篇一块"占多大比例；
2. **块粒度**：`token_count` 的分布与 <256（`DEFAULT_MIN_TOKENS`）占比 → 是否过碎；
3. **边覆盖**：带边块数/边数，以及"单块文档占比 vs 带边块占比"的关系。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/p38_kb_structure_health.py \
        --kb-version kb_v1.3.0 --report ~/uai/reports/kb_structure_health.json
    # 追加：跨文档同章节边的**候选池量化**（决定"要不要按这条规则扩边"）
    ~/uai/venv/bin/python scripts/audit/p38_kb_structure_health.py --coverage

退出码：0 有结论；2 连不上库/没有该 kb 版本的数据。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _collect(kb_version: str) -> dict:
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        total = await conn.fetchval(
            "SELECT count(*) FROM knowledge_chunks WHERE kb_version = $1", kb_version
        )
        if not total:
            raise SystemExit(f"[FATAL] kb_version={kb_version} 在 knowledge_chunks 里没有数据")

        docs = await conn.fetch(
            "SELECT source_doc_id, count(*) AS n FROM knowledge_chunks"
            " WHERE kb_version = $1 GROUP BY source_doc_id",
            kb_version,
        )
        n_docs = len(docs)
        single = sum(1 for d in docs if d["n"] == 1)
        two_three = sum(1 for d in docs if 2 <= d["n"] <= 3)
        four_plus = sum(1 for d in docs if d["n"] >= 4)
        max_chunks = max((d["n"] for d in docs), default=0)

        tokens = await conn.fetch(
            "SELECT token_count FROM knowledge_chunks WHERE kb_version = $1", kb_version
        )
        tok = sorted(int(t["token_count"] or 0) for t in tokens)
        n_tok = len(tok)

        def q(p: float) -> int:
            if not tok:
                return 0
            idx = min(n_tok - 1, max(0, int(p * (n_tok - 1))))
            return tok[idx]

        edges = await conn.fetch(
            "SELECT count(*) AS n FROM knowledge_chunks"
            " WHERE kb_version = $1 AND jsonb_typeof(metadata->'relations') = 'array'",
            kb_version,
        )
        chunks_with_edges = int(edges[0]["n"] or 0)
        edge_total = await conn.fetchval(
            "SELECT COALESCE(sum(jsonb_array_length(metadata->'relations')), 0)"
            " FROM knowledge_chunks"
            " WHERE kb_version = $1 AND jsonb_typeof(metadata->'relations') = 'array'",
            kb_version,
        )
        organ_rows = await conn.fetch(
            "SELECT organ, count(*) AS n FROM knowledge_chunks"
            " WHERE kb_version = $1 GROUP BY organ ORDER BY n DESC",
            kb_version,
        )
        sec_nonempty = await conn.fetchval(
            "SELECT count(*) FROM knowledge_chunks"
            " WHERE kb_version = $1 AND COALESCE(section_path, '') <> ''",
            kb_version,
        )
    finally:
        await conn.close()

    # 结论（结构化的"所以然"，避免只给一堆数字）
    single_ratio = round(single / n_docs, 4) if n_docs else 0.0
    small_ratio = round(sum(1 for t in tok if t < 256) / n_tok, 4) if n_tok else 0.0
    edge_ratio = round(chunks_with_edges / total, 4) if total else 0.0
    if single_ratio >= 0.5:
        diagnosis = (
            "MAJORITY_SINGLE_CHUNK_DOCS：过半文档只有一块 → 这些文档**结构上不可能有图边**，"
            "图谱扩展的适用面天然受限；改 overlap 帮助有限，要扩适用面得从**语料结构**"
            "（长文档入库/按标题切分）或**边规则**（跨文档/章节级）入手"
        )
    elif small_ratio >= 0.3:
        diagnosis = (
            "TOO_MANY_SMALL_CHUNKS：块普遍偏小（<256 token 占比高）→ 切分过碎，"
            "可考虑提高 min_tokens / 加 overlap 让块内上下文更完整"
        )
    else:
        diagnosis = (
            "STRUCTURE_OK_BUT_GOLDEN_HITS_SINGLE_CHUNK：结构本身不算畸形，"
            "问题在**命中分布**（golden 命中的是单块对话语料）→ 修切分收益有限，"
            "应先修检索/语料"
        )

    return {
        "kb_version": kb_version,
        "total_chunks": int(total),
        "documents": {
            "n": n_docs,
            "single_chunk": single,
            "two_to_three": two_three,
            "four_plus": four_plus,
            "max_chunks_per_doc": max_chunks,
            "single_ratio": single_ratio,
        },
        "token_count": {
            "min": q(0.0),
            "p25": q(0.25),
            "median": q(0.5),
            "p75": q(0.75),
            "max": q(1.0),
            "below_256_ratio": small_ratio,
        },
        "edges": {
            "chunks_with_edges": chunks_with_edges,
            "chunks_with_edges_ratio": edge_ratio,
            "edge_total": int(edge_total or 0),
            "rule": "同 source_doc_id 内相邻块互指（builder._attach_relations / kb_backfill_relations.py）",
        },
        "organs": {r["organ"]: int(r["n"]) for r in organ_rows},
        "section_path_nonempty_ratio": round(int(sec_nonempty or 0) / total, 4) if total else 0.0,
        "diagnosis": diagnosis,
    }


async def _coverage(kb_version: str) -> dict:
    """跨文档同章节边的**候选池量化**：预测适用面 + 超级节点风险。

    规则候选：`group = (organ, section_path)`，组内块跨文档互指。
    对 136 条 golden 查询取 top-3 种子，统计：
    * 「至少一个种子能拿到跨文档新邻居」的查询数（= 预测适用面）；
    * 平均每个种子有多少候选（**候选过多 = 只能任取 5 个，噪声风险**）。
    """
    import asyncpg

    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.rag.protocols import RetrievalRequest
    from app.rag.retriever import Retriever

    gold = json.loads(
        (pathlib.Path.home() / "uai/data/golden/kb_qa_golden_v1.json").read_text(encoding="utf-8")
    )
    queries = gold.get("queries", gold)
    client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    retriever = Retriever(
        vector_client=client, embedding_service=emb, ultrasound_clip=None,
        bm25_index=None, session_factory=None,
    )
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    n_q = queries_with_gain = seeds_total = seeds_with_cross = candidates_total = 0
    try:
        for q in queries:
            req = RetrievalRequest(
                query=q["query_text"], top_k=10, use_query_enhancement=False,
                use_reranking=False, use_bm25_fallback=False, use_multimodal=False,
            )
            res = await retriever.retrieve(req, enhanced_query=q["query_text"])
            top = [sc.chunk.chunk_id for sc in res.chunks]
            n_q += 1
            gain = 0
            for sid in top[:3]:
                seeds_total += 1
                row = await conn.fetchrow(
                    "SELECT organ, section_path FROM knowledge_chunks WHERE chunk_id = $1", sid
                )
                if row is None:
                    continue
                group = await conn.fetch(
                    "SELECT chunk_id FROM knowledge_chunks WHERE kb_version = $1"
                    " AND organ = $2 AND COALESCE(section_path,'') = COALESCE($3,'')",
                    kb_version, row["organ"], row["section_path"],
                )
                others = [g["chunk_id"] for g in group if g["chunk_id"] not in top]
                if others:
                    seeds_with_cross += 1
                    candidates_total += len(others)
                    gain += len(others)
            if gain:
                queries_with_gain += 1
    finally:
        await conn.close()

    per_seed = round(candidates_total / seeds_with_cross, 1) if seeds_with_cross else 0.0
    if per_seed >= 20:
        verdict = (
            "SUPERNODE_RISK：候选池过大（每种子上百个），一次只能取 5 个 → 等于任取，"
            "**先做邻居排序（相似度/相关性选 top-k）再谈扩边**；直接按此规则落库＝往检索里灌噪声"
        )
    elif n_q and queries_with_gain / n_q >= 0.2:
        verdict = "COVERAGE_OK：适用面达标，可进 rc 版本做指标对照"
    else:
        verdict = "COVERAGE_LOW：适用面仍不足，扩边收益有限"
    return {
        "mode": "cross_doc_section_coverage",
        "kb_version": kb_version,
        "queries": n_q,
        "queries_with_gain": queries_with_gain,
        "coverage_ratio": round(queries_with_gain / n_q, 4) if n_q else 0.0,
        "seeds_total": seeds_total,
        "seeds_with_cross_doc_neighbor": seeds_with_cross,
        "candidates_total": candidates_total,
        "candidates_per_seed": per_seed,
        "verdict": verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/kb_structure_health.json"))
    ap.add_argument(
        "--coverage",
        action="store_true",
        help="追加跨文档同章节边的候选池量化（需要检索器与 DB；结论决定'要不要按这条规则扩边'）",
    )
    ap.add_argument(
        "--coverage-report",
        default=str(pathlib.Path.home() / "uai/reports/kb_graph_coverage.json"),
        help="--coverage 模式的报告路径（与结构体检分开，避免互相覆盖）",
    )
    args = ap.parse_args()

    if args.coverage:
        t0c = time.monotonic()
        try:
            cov = asyncio.run(_coverage(args.kb_version))
        except Exception as exc:
            print(f"[FATAL] 候选池量化失败：{type(exc).__name__}: {exc}")
            return 2
        cov["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        cov["probe"] = "p38_kb_structure_health --coverage"
        cov["wall_s"] = round(time.monotonic() - t0c, 1)
        out = pathlib.Path(args.coverage_report).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"跨文档同章节边：预测适用面 {cov['queries_with_gain']}/{cov['queries']}"
            f"（{cov['coverage_ratio']:.1%}）· 种子 {cov['seeds_with_cross_doc_neighbor']}"
            f"/{cov['seeds_total']} 有跨文档邻居 · 候选池 {cov['candidates_total']} 条"
            f"（每种子 {cov['candidates_per_seed']}）"
        )
        print(f"判定 = {cov['verdict']}")
        print(f"报告：{out}")
        return 0

    t0 = time.monotonic()
    try:
        payload = asyncio.run(_collect(args.kb_version))
    except SystemExit as exc:
        print(str(exc))
        return 2
    except Exception as exc:
        print(f"[FATAL] 结构体检失败：{type(exc).__name__}: {exc}")
        return 2

    payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["probe"] = "p38_kb_structure_health"
    payload["wall_s"] = round(time.monotonic() - t0, 1)
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    d = payload["documents"]
    t = payload["token_count"]
    e = payload["edges"]
    print(f"{payload['kb_version']}：块 {payload['total_chunks']} · 文档 {d['n']}"
          f"（单块 {d['single_chunk']} = {d['single_ratio']:.1%}｜2-3 块 {d['two_to_three']}"
          f"｜4+ 块 {d['four_plus']}｜最多 {d['max_chunks_per_doc']} 块）")
    print(f"token：min {t['min']} / p25 {t['p25']} / 中位 {t['median']} / p75 {t['p75']} / max {t['max']}"
          f"（<256 占 {t['below_256_ratio']:.1%}）")
    print(f"边：带边块 {e['chunks_with_edges']}（{e['chunks_with_edges_ratio']:.1%}）/ 边 {e['edge_total']} 条")
    print(f"section_path 非空占比 {payload['section_path_nonempty_ratio']:.1%}")
    print(f"诊断 = {payload['diagnosis'][:120]}...")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
