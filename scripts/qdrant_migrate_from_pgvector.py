"""把 pgvector `vector_index_knowledge_chunks` 全量迁移到 Qdrant（A.50.8 E6）。

背景：kb 灌数后 embedding 存在 pgvector 的 vector_index 表（4526 行），
knowledge_chunks.embedding 列为 NULL——populate 脚本从后者读会全空。
本脚本直接以 pgvector 行为源，经 create_index_client 写入 Qdrant
（settings.vector_index_backend=qdrant），id/metadata 原样迁移。

用法（真机 ~/uai/src，source ~/uai/.env 后）：
    PYTHONPATH=~/uai/src python scripts/qdrant_migrate_from_pgvector.py \
        --collection knowledge_chunks [--batch 500]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.dsn import to_asyncpg_dsn
from app.config.settings import settings


def _parse_vector(raw: object) -> list[float]:
    if raw is None:
        raise ValueError("embedding is NULL")
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    text = str(raw).strip().strip("[]")
    if not text or text.lower() == "none":
        raise ValueError("embedding is empty")
    return [float(x) for x in text.split(",") if x.strip()]


async def main_async(collection: str, batch: int) -> int:
    import asyncpg

    from app.embedding.index_factory import create_index_client_from_settings
    from app.embedding.protocols import VectorRecord  # type: ignore[attr-defined]

    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    total = await conn.fetchval(
        "SELECT count(*) FROM vector_index_knowledge_chunks"
    )
    print(f"source rows: {total}")
    client = await create_index_client_from_settings()
    await client.create_collection(collection, dimension=1024)

    migrated, skipped = 0, 0
    offset = 0
    while True:
        rows = await conn.fetch(
            # ::text 强转：asyncpg 对 pgvector 类型返回二进制对象，
            # str() 不是 '[...]' 文本（A.50.8 实测 migrated=0 根因）
            "SELECT id, embedding::text AS embedding, metadata "
            "FROM vector_index_knowledge_chunks ORDER BY id LIMIT $1 OFFSET $2",
            batch,
            offset,
        )
        if not rows:
            break
        records = []
        for r in rows:
            try:
                # jsonb 经 asyncpg 返回 str（未注册 codec）——需 json.loads
                meta = r["metadata"]
                if isinstance(meta, str):
                    meta = json.loads(meta) if meta else {}
                records.append(
                    VectorRecord(
                        id=r["id"],
                        vector=_parse_vector(r["embedding"]),
                        metadata=meta or {},
                    )
                )
            except ValueError as exc:
                skipped += 1
                if skipped <= 3:
                    print(f"  skip row {r['id']}: {exc}")
        if records:
            await client.upsert(collection, records)
            migrated += len(records)
        offset += batch
    await conn.close()
    print(f"migrated {migrated} records (skipped_no_embedding={skipped}) -> qdrant")
    return 0 if migrated else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="pgvector → Qdrant 迁移")
    parser.add_argument("--collection", default=settings.rag_collection)
    parser.add_argument("--batch", type=int, default=500)
    args = parser.parse_args()
    return asyncio.run(main_async(args.collection, args.batch))


if __name__ == "__main__":
    main()
