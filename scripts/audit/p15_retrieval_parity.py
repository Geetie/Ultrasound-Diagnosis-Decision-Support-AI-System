#!/usr/bin/env python3
"""A.51 检索口径闭合（E6 遗留）：eval 直算口径 vs 生产服务路径口径 + kb_version 三处核查。

背景（A.50.8 登记）：qdrant 已上线并服务判读，但 Gate 2 的 hit@10=98.53% 来自
`scripts/gate2_bge_top10.py`——直连 PG 语料 + 进程内余弦（**不含** RRF/回表/
版本过滤）。生产判读走 `Retriever`（qdrant 向量腿 + BM25 稀疏腿 + RRF + rerank）。
两者是否等价从未验证；另 `settings.rag_kb_version` 默认 kb_v1.0.0 而 Gate 2
用 kb_v1.3.0，qdrant collection 又无版本过滤——三处口径必须一次查清。

本探针只读（不写任何生产状态），产出 reports/retrieval_parity_<ts>.json：
  1. kb_version 口径三处核查：.env / PG 两表分布 / qdrant payload 分布
  2. 136 golden 双口径 hit@1/5/10 + MRR：
     - eval 腿：复现 gate2（选定 kb_version 的全量语料 + 进程内余弦）
     - 服务腿：进程内构造生产 Retriever（dense-only，top_k=10）→ 回表 chunk →
       source_doc_id 级命中（与 eval 腿同判定式）
  3. 判定：|hit@10 差| ≤ 2pp（低侧差 > 2pp 视为服务路径退化，需定位）

前置：8004 嵌入服务、PG(15432)、qdrant(6333)、`source ~/uai/.env`。
运行（真机）：
    cd ~/uai/src && set -a && source ~/uai/.env && set +a && \
      ~/uai/venv/bin/python scripts/audit/p15_retrieval_parity.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

from _audit_common import check, summary  # noqa: E402
from t1_eval_retrieval import cosine_topk, embed_texts  # noqa: E402

GOLDEN = pathlib.Path.home() / "uai" / "data" / "golden" / "kb_qa_golden_v1.json"
BGE_URL = "http://127.0.0.1:8004"
TOP_K = 10
DIFF_LIMIT_PP = 2.0


def _pg_dsn() -> str:
    from app.config.settings import settings

    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


# ----------------------------------------------------------------------
# 1) kb_version 口径三处核查
# ----------------------------------------------------------------------


def env_kb_version() -> str:
    """~/uai/.env 的 RAG_KB_VERSION（生产启动脚本 source 的对象）。"""
    env_file = pathlib.Path.home() / "uai" / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("RAG_KB_VERSION="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


async def pg_distributions() -> dict:
    import asyncpg

    conn = await asyncpg.connect(_pg_dsn())
    try:
        kc = await conn.fetch(
            "SELECT kb_version, count(*) AS n FROM knowledge_chunks GROUP BY 1 ORDER BY 2 DESC"
        )
        vi = await conn.fetch(
            "SELECT metadata->>'kb_version' AS kb, count(*) AS n "
            "FROM vector_index_knowledge_chunks GROUP BY 1 ORDER BY 2 DESC"
        )
    finally:
        await conn.close()
    return {
        "knowledge_chunks": [(r["kb_version"], r["n"]) for r in kc],
        "vector_index": [(r["kb"], r["n"]) for r in vi],
    }


async def qdrant_distribution(collection: str) -> dict:
    """qdrant payload.kb_version 分布（REST scroll 分页，只读）。"""
    import httpx

    counts: dict[str, int] = {}
    total = 0
    offset = None
    async with httpx.AsyncClient(timeout=30.0) as c:
        while True:
            body: dict = {"limit": 1000, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            r = await c.post(
                f"http://127.0.0.1:6333/collections/{collection}/points/scroll",
                json=body,
            )
            if r.status_code != 200:
                return {"error": f"http={r.status_code} {r.text[:120]}"}
            data = r.json()["result"]
            points = data.get("points", [])
            for p in points:
                kb = (p.get("payload") or {}).get("kb_version", "<missing>")
                counts[kb] = counts.get(kb, 0) + 1
            total += len(points)
            offset = data.get("next_page_offset")
            if not offset or not points:
                break
    return {"total": total, "by_kb_version": sorted(counts.items(), key=lambda x: -x[1])}


# ----------------------------------------------------------------------
# 2) 两条腿
# ----------------------------------------------------------------------


async def load_corpus(kb_version: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(_pg_dsn())
    try:
        rows = await conn.fetch(
            "SELECT chunk_id, source_doc_id, content FROM knowledge_chunks WHERE kb_version = $1",
            kb_version,
        )
    finally:
        await conn.close()
    return [
        {"chunk_id": r["chunk_id"], "source_doc_id": r["source_doc_id"], "text": r["content"]}
        for r in rows
    ]


def eval_leg(queries: list[dict], corpus: list[dict]) -> dict:
    """复现 gate2 口径：进程内余弦 + source_doc_id 级命中。"""
    q_vecs = embed_texts([q["query_text"] for q in queries], BGE_URL)
    c_vecs = embed_texts([c["text"] for c in corpus], BGE_URL)
    rankings = cosine_topk(q_vecs, c_vecs, k=TOP_K)
    hits = {1: 0, 5: 0, 10: 0}
    mrr = 0.0
    for q, ranking in zip(queries, rankings, strict=False):
        gold = q["relevant_source_doc_id"]
        docs = [corpus[idx]["source_doc_id"] for idx, _ in ranking]
        for k in hits:
            if gold in docs[:k]:
                hits[k] += 1
        for rank, doc in enumerate(docs, start=1):
            if doc == gold:
                mrr += 1.0 / rank
                break
    n = len(queries)
    return {
        "hit_at_1": round(hits[1] / n, 4),
        "hit_at_5": round(hits[5] / n, 4),
        "hit_at_10": round(hits[10] / n, 4),
        "mrr": round(mrr / n, 4),
        "corpus_chunks": len(corpus),
    }


async def service_leg(queries: list[dict]) -> dict:
    """生产服务路径：真实 Retriever（qdrant 向量腿，dense-only）回表取 chunk。"""
    from app.config.database import AsyncSessionLocal
    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.rag.protocols import RetrievalRequest
    from app.rag.retriever import Retriever

    client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    retriever = Retriever(
        vector_client=client,
        embedding_service=emb,
        ultrasound_clip=None,
        bm25_index=None,  # dense-only：与 eval 腿同构对照
        session_factory=AsyncSessionLocal,
    )

    hits = {1: 0, 5: 0, 10: 0}
    mrr = 0.0
    misses: list[dict] = []
    other_kb: dict[str, int] = {}
    for q in queries:
        req = RetrievalRequest(
            query=q["query_text"],
            top_k=TOP_K,
            use_query_enhancement=False,  # 与 eval 腿同构：不增强、不重排
            use_reranking=False,
            use_bm25_fallback=False,
            use_multimodal=False,
        )
        res = await retriever.retrieve(req, enhanced_query=q["query_text"])
        docs = [sc.chunk.source_doc_id for sc in res.chunks]
        for sc in res.chunks:
            kb = (sc.chunk.metadata or {}).get("kb_version")
            if kb:
                other_kb[kb] = other_kb.get(kb, 0) + 1
        gold = q["relevant_source_doc_id"]
        for k in hits:
            if gold in docs[:k]:
                hits[k] += 1
        rank_hit = 0
        for rank, doc in enumerate(docs, start=1):
            if doc == gold:
                mrr += 1.0 / rank
                rank_hit = rank
                break
        if rank_hit == 0:
            misses.append({"query": q["query_text"][:60], "gold": gold, "top10_docs": docs[:10]})
    n = len(queries)
    return {
        "hit_at_1": round(hits[1] / n, 4),
        "hit_at_5": round(hits[5] / n, 4),
        "hit_at_10": round(hits[10] / n, 4),
        "mrr": round(mrr / n, 4),
        "miss_count": len(misses),
        "miss_samples": misses[:5],
        "retrieved_chunk_kb_versions": sorted(other_kb.items(), key=lambda x: -x[1]),
    }


async def main() -> int:
    print("== 1) kb_version 口径三处核查 ==", flush=True)
    from app.config.settings import settings

    env_kb = env_kb_version()
    print(f"  .env RAG_KB_VERSION = {env_kb!r}")
    print(f"  settings.rag_kb_version = {settings.rag_kb_version!r}")
    print(f"  settings.rag_collection = {settings.rag_collection!r}")
    dist = await pg_distributions()
    print(f"  PG knowledge_chunks: {dist['knowledge_chunks']}")
    print(f"  PG vector_index: {dist['vector_index']}")
    qdist = await qdrant_distribution(f"ultrasound_{settings.rag_collection}")
    print(f"  qdrant payload: {qdist}")

    check("口径一致性：.env 与 settings 的 kb_version 相同",
          env_kb == settings.rag_kb_version or not env_kb,
          f"env={env_kb!r} settings={settings.rag_kb_version!r}")
    qdrant_kbs = [kb for kb, _ in qdist.get("by_kb_version", []) if kb != "<missing>"]
    check("qdrant 单调版本（无跨版本混灌）", len(qdrant_kbs) <= 1,
          f"qdrant kb_versions={qdrant_kbs}")

    print("== 2) 双口径对比（136 golden）==", flush=True)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = golden["queries"]
    print(f"  golden queries: {len(queries)}")

    kb_for_eval = settings.rag_kb_version
    t0 = time.time()
    corpus = await load_corpus(kb_for_eval)
    if not corpus:
        # 口径兜底：settings 版本无语料时退回 Gate 2 参考版本（真机常见：
        # .env 未设 RAG_KB_VERSION → 默认 kb_v1.0.0，而语料实际在 kb_v1.3.0）
        print(f"  WARN: kb={kb_for_eval} 语料为空，退回 kb_v1.3.0（Gate 2 参考版本）")
        kb_for_eval = "kb_v1.3.0"
        corpus = await load_corpus(kb_for_eval)
    print(f"  eval 语料: {len(corpus)} chunks (kb={kb_for_eval}) [{time.time()-t0:.1f}s]")
    if not corpus:
        check("eval 语料非空", False, f"kb_version={kb_for_eval} 无 chunks")
        return summary("A.51 检索口径闭合")

    t0 = time.time()
    ev = eval_leg(queries, corpus)
    print(f"  eval 腿: {ev} [{time.time()-t0:.1f}s]", flush=True)
    # Gate 2 参考口径（kb_v1.3.0）额外复跑一次（若与现行版本不同）
    gate2_reference: dict | None = None
    if kb_for_eval != "kb_v1.3.0":
        alt_corpus = await load_corpus("kb_v1.3.0")
        if alt_corpus:
            gate2_reference = eval_leg(queries, alt_corpus)
            print(f"  Gate2 参考（kb_v1.3.0）: {gate2_reference}", flush=True)

    t0 = time.time()
    sv = await service_leg(queries)
    print(f"  服务腿: hit@10={sv['hit_at_10']} mrr={sv['mrr']} miss={sv['miss_count']} "
          f"[{time.time()-t0:.1f}s]", flush=True)

    diff_pp = round((sv["hit_at_10"] - ev["hit_at_10"]) * 100, 2)
    check(f"服务路径 hit@10 与 eval 口径差 ≤{DIFF_LIMIT_PP}pp",
          abs(diff_pp) <= DIFF_LIMIT_PP,
          f"eval={ev['hit_at_10']} service={sv['hit_at_10']} diff={diff_pp:+.2f}pp")

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "golden": str(GOLDEN),
        "n_queries": len(queries),
        "kb_version_check": {
            "env": env_kb,
            "settings": settings.rag_kb_version,
            "pg": dist,
            "qdrant": qdist,
        },
        "eval_leg": ev,
        "eval_kb_version": kb_for_eval,
        "gate2_reference_kb_v1_3_0": gate2_reference,
        "service_leg": sv,
        "diff_pp": diff_pp,
        "verdict": "PASS" if abs(diff_pp) <= DIFF_LIMIT_PP else "FAIL",
    }
    out_path = pathlib.Path.home() / "uai" / "reports" / (
        f"retrieval_parity_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {out_path}")
    return summary("A.51 检索口径闭合")


if __name__ == "__main__":
    if os.environ.get("P15_DEBUG"):
        print("debug mode")
    sys.exit(asyncio.run(main()))
