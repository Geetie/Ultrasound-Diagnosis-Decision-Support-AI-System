#!/usr/bin/env python3
"""A.51：把指定 kb_version 的 knowledge_chunks 重新嵌入并灌入 Qdrant（生产向量索引）。

背景（A.51 真机口径核查发现的两个真缺陷）：
1. `qdrant_client.py` 的 `search()` 在 qdrant-client ≥1.14 被移除（改
   `query_points`）→ 生产 dense 向量腿静默全空（已修，见该文件注释）；
2. qdrant collection（`ultrasound_knowledge_chunks`）里只有 5 个 **旧版本**
   payload（kb_v1.0.0~v1.2.0，3269 点，来自 PG vector_index 历史行），
   **没有当前生产版本 kb_v1.3.0 的向量**——dense 腿即便查询成功也检索不到
   现行知识（旧点回表时 chunk_id 不在 knowledge_chunks → 被丢弃）。

本脚本用**生产同款 embedder**（BgeM3MedicalService → 8004）对 KB 文本重新
嵌入，写入 Qdrant（metadata 带 kb_version/organ/source_doc_id），并可清理
非目标版本的旧点。

用法（真机，先 `set -a; source ~/uai/.env; set +a`）：
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/qdrant_reindex_kb.py \
        --kb-version kb_v1.3.0 --purge-others
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config.dsn import to_asyncpg_dsn
from app.config.settings import settings


async def fetch_chunks(kb_version: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(to_asyncpg_dsn(settings.database_url))
    try:
        rows = await conn.fetch(
            """
            SELECT chunk_id, organ, source_doc_id, section_path, content,
                   metadata->>'chunk_index' AS chunk_index
            FROM knowledge_chunks WHERE kb_version = $1 ORDER BY chunk_id
            """,
            kb_version,
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def purge_other_versions(collection: str, keep_kb: str) -> int:
    """删除 collection 中 kb_version != keep_kb 的点（REST filter delete）。"""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            f"http://127.0.0.1:6333/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={
                "filter": {
                    "must_not": [{"key": "kb_version", "match": {"value": keep_kb}}]
                }
            },
        )
        if r.status_code != 200:
            raise RuntimeError(f"purge failed: http={r.status_code} {r.text[:200]}")
        return int(r.json().get("result", {}).get("deleted", 0) or 0)


async def main_async(kb_version: str, purge_others: bool, batch: int) -> int:
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.embedding.protocols import (
        TextEmbeddingRequest,
        VectorRecord,  # type: ignore[attr-defined]
    )

    chunks = await fetch_chunks(kb_version)
    print(f"knowledge_chunks({kb_version}): {len(chunks)}")
    if not chunks:
        print("ERROR: 无 chunks")
        return 1

    embedder = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    client = await create_index_client_from_settings()
    collection = settings.rag_collection

    vectors: list[list[float]] = []
    total = len(chunks)
    for i in range(0, total, batch):
        part = chunks[i : i + batch]
        resp = await embedder.embed_text(
            TextEmbeddingRequest(texts=[c["content"] for c in part])
        )
        if len(resp.embeddings) != len(part):
            raise RuntimeError(
                f"embedding count mismatch: {len(resp.embeddings)} != {len(part)}"
            )
        vectors.extend([float(x) for x in v] for v in resp.embeddings)
        print(f"  embedded {min(i + batch, total)}/{total}", flush=True)

    records = [
        VectorRecord(
            id=c["chunk_id"],
            vector=vectors[i],
            metadata={
                "organ": c["organ"],
                "kb_version": kb_version,
                "source_doc_id": c["source_doc_id"],
                "section_path": c["section_path"] or "",
                "chunk_index": int(c["chunk_index"] or 0),
            },
        )
        for i, c in enumerate(chunks)
    ]
    await client.create_collection(collection, dimension=len(records[0].vector))
    for i in range(0, len(records), batch):
        await client.upsert(collection, records[i : i + batch])
    print(f"upserted {len(records)} vectors -> {collection} (kb={kb_version})")

    if purge_others:
        deleted = await purge_other_versions(f"ultrasound_{collection}", kb_version)
        print(f"purged {deleted} stale points (kb_version != {kb_version})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="KB chunks → Qdrant 生产向量索引（重新嵌入）")
    ap.add_argument("--kb-version", required=True)
    ap.add_argument("--purge-others", action="store_true",
                    help="删除 collection 中其它 kb_version 的旧点")
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()
    return asyncio.run(main_async(args.kb_version, args.purge_others, args.batch))


if __name__ == "__main__":
    raise SystemExit(main())
