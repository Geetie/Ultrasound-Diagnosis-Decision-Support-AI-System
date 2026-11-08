"""把 knowledge_chunks 的 KB chunks 灌入生产检索器的向量索引表。

背景（真机 2026-09-29 T1b 实测的接线缺口）：`init_knowledge_base.py` 只写
`knowledge_chunks`（KB 管理侧，含 embedding 列），而生产诊断检索器
（app/rag/retriever.py）读的是 **`vector_index_{rag_collection}`**（即
`vector_index_knowledge_chunks`）——建库后该表为空，dense 检索什么都查不到。
本脚本经 `app.embedding.index_factory.create_index_client_from_settings` 生产客户端灌数（幂等）——
后端按 `vector_index_backend` 选择：qdrant（默认，A.50.8 上线）或降级 pgvector。
无嵌入的 chunk 行自动跳过（不拖垮整批）。

2026-11-08 修复：本脚本曾 import 不存在的 `from_settings`（真实名
`create_index_client_from_settings`），每次执行必 ImportError，**从未成功跑通**。
真机实测因此留下硬伤：`vector_index_knowledge_chunks` 只到 `kb_v1.2.0`，
生产在用的 `kb_v1.3.0` 1257 条**从未进过向量索引** → dense 检索腿对 v1.3 内容恒空，
只剩 BM25 稀疏腿兜底。B2 检索集里"检索不到"的条目有一部分是这里的锅，不是语料缺口。

用法（真机 ~/uai/src）：
    PYTHONPATH=~/uai/src python scripts/server_populate_vector_index.py \
        --kb-version kb_v1.0.0 --organ thyroid
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config.dsn import to_asyncpg_dsn  # noqa: E402
from app.config.settings import settings  # noqa: E402
from app.embedding.protocols import VectorRecord  # noqa: E402

logger = logging.getLogger(__name__)


def _parse_vector(raw: object) -> list[float]:
    """asyncpg 返回 pgvector 为字符串 '[a,b,...]'；列表形态则直接转。

    NULL/空向量抛 ValueError —— 调用方按行跳过（部分 chunk 无嵌入是
    正常态，A.50.8 灌数实测 4526 行中存在）。
    """
    if raw is None:
        raise ValueError("embedding is NULL")
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    text = str(raw).strip().strip("[]")
    if not text or text.lower() == "none":
        raise ValueError("embedding is empty")
    return [float(x) for x in text.split(",") if x.strip()]


async def main_async(kb_version: str, organ: str, purge: bool) -> None:
    import asyncpg

    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    if purge:
        # 清除该 kb_version 的旧索引行（chunk_id 方案变更/重建后防陈旧残留）
        deleted = await conn.execute(
            """
            DELETE FROM vector_index_knowledge_chunks
            WHERE metadata->>'kb_version' = $1
            """,
            kb_version,
        )
        print(f"purged: {deleted}")
    rows = await conn.fetch(
        """
        SELECT chunk_id, embedding, organ, source_doc_id, section_path,
               metadata->>'chunk_index' AS chunk_index
        FROM knowledge_chunks
        WHERE kb_version = $1 AND ($2 = '' OR organ = $2)
        """,
        kb_version,
        organ,
    )
    await conn.close()
    if not rows:
        print(f"ERROR: kb_version={kb_version} organ={organ} 无 chunks")
        raise SystemExit(1)

    records: list[VectorRecord] = []
    skipped = 0
    for r in rows:
        try:
            vector = _parse_vector(r["embedding"])
        except ValueError:
            logger.warning("main_async: 未预期异常，按原逻辑降级", exc_info=True)
            skipped += 1  # 部分 chunk 无嵌入（正常态），跳过不拖垮整批
            continue
        records.append(
            VectorRecord(
                id=r["chunk_id"],
                vector=vector,
                metadata={
                    "organ": r["organ"],
                    "kb_version": kb_version,
                    "source_doc_id": r["source_doc_id"],
                    "section_path": r["section_path"] or "",
                    "chunk_index": int(r["chunk_index"] or 0),
                },
            )
        )
    if not records:
        print(f"ERROR: kb_version={kb_version} 无可灌入行（{skipped} 行无嵌入）")
        raise SystemExit(1)
    # 走工厂：vector_index_backend=qdrant 时写 Qdrant，不可用自动降级 pgvector
    #
    # 2026-11-08 修复：这里此前 `from app.embedding.index_factory import from_settings`，
    # 而该模块**没有** `from_settings`（真实名是 `create_index_client_from_settings`，
    # 见 index_factory.__all__）→ 每次执行必 ImportError，脚本从未成功跑通过。
    # 后果：`knowledge_chunks` 有 embedding，生产向量索引却是空的 ——
    # 真机实测 `vector_index_knowledge_chunks` 最新只到 kb_v1.2.0，
    # `kb_v1.3.0` 的 1257 条**从未入索引**（dense 腿查不到任何 v1.3 内容）。
    # 这是 B2 检索集"检索不到"类问题的**基础设施层**根因之一，不是数据缺口。
    from app.embedding.index_factory import create_index_client_from_settings

    client = await create_index_client_from_settings()
    await client.create_collection(
        settings.rag_collection, dimension=len(records[0].vector)
    )
    await client.upsert(settings.rag_collection, records)
    print(
        f"populated {len(records)} records (skipped_no_embedding={skipped}) "
        f"-> backend={type(client).__name__} collection={settings.rag_collection}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="KB chunks → 生产向量索引")
    parser.add_argument("--kb-version", required=True)
    parser.add_argument("--organ", default="", help="过滤器官（空=全部）")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="灌数前先删除该 kb_version 的旧索引行（重建后防陈旧残留）",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.kb_version, args.organ, args.purge))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
