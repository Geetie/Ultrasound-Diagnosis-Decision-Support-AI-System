"""把 source_set 回填进 knowledge_chunks.metadata 与向量索引 payload（2026-11-09）。

## 什么时候需要跑

``source_set`` 是**纯派生字段**：真值在 ``app/knowledge/source_set.py`` 的规则表里，
运行时用 ``source_set_of(source_doc_id)`` 现算。所以：

* **BM25 腿**：根本不需要这个脚本（建索引时现算，见 ``bm25_fallback._build_locked``）；
* **并列渲染**：不需要（``source_conflict`` 现算）；
* **dense 腿的 Qdrant filter**：**需要**。``filter={"source_set": [...]}`` 是拿
  payload 里的字段做条件，payload 里没有这个键 → 恒 0 命中。所以要让"医院过滤"
  在 dense 腿上也生效，必须回填 payload。

不跑这一趟的后果是**可控降级**而不是静默错误：BM25 腿照常过滤、并列照常渲染，
只有 dense 腿的语料集收窄不生效。这比"过滤把结果全清空"安全，
但**会让医院以为限制生效了**，所以部署时应当跑一次。

## 两种后端

| 后端 | 回填动作 |
|---|---|
| PG ``knowledge_chunks`` | ``UPDATE metadata = metadata \\|\\| {"source_set": ...}`` |
| PG ``vector_index_knowledge_chunks`` | 同上（PGVector 后端直接吃这张表） |
| Qdrant | ``set_payload``（按 chunk_id 逐批） |

## 安全约定

* **默认 dry-run**：只统计"会改多少行"，不写库。要真改必须显式 ``--apply``。
* 只**新增** ``metadata.source_set`` 键，**不动**其它任何字段。
* 不删任何行、不改 ``content`` / ``embedding`` / ``source_doc_id``。
* 未知来源统一写 ``unknown``（**不猜**），并在结果里按 doc_prefix 分组列出，
  方便人工决定要不要往规则表里补一条。

用法（远端）::

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/kb_backfill_source_set.py \
        --kb-version kb_v1.5.0            # 先看 dry-run 统计
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/kb_backfill_source_set.py \
        --kb-version kb_v1.5.0 --apply    # 确认后写库
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/kb_backfill_source_set.py \
        --kb-version kb_v1.5.0 --apply --qdrant   # 一并回填向量索引 payload
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import settings  # noqa: E402
from app.knowledge.source_set import (  # noqa: E402
    SOURCE_SET_UNKNOWN,
    source_set_of,
)


def _dsn() -> str:
    """取 asyncpg 可用的 DSN（去掉 SQLAlchemy 的 ``+asyncpg`` 后缀）。"""
    raw = os.environ.get("DATABASE_URL") or settings.database_url
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", str(raw))


async def _backfill_pg(kb_version: str | None, apply: bool, table: str) -> dict:
    """回填一张 PG 表里 metadata 的 source_set。返回统计。"""
    import asyncpg

    conn = await asyncpg.connect(_dsn())
    try:
        where, params = "", []
        if kb_version:
            params.append(kb_version)
            where = "WHERE kb_version = $1" if table == "knowledge_chunks" else "WHERE metadata->>'kb_version' = $1"
        rows = await conn.fetch(
            f"SELECT chunk_id, source_doc_id, metadata FROM {table} {where}", *params
        )
        stats: dict[str, int] = {}
        by_prefix: dict[str, int] = {}
        updates: list[tuple[str, str]] = []
        for row in rows:
            meta = row["metadata"]
            meta = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
            doc_id = row["source_doc_id"] or meta.get("source_doc_id") or ""
            ss = source_set_of(source_doc_id=doc_id, metadata=meta)
            stats[ss] = stats.get(ss, 0) + 1
            if ss == SOURCE_SET_UNKNOWN and doc_id:
                prefix = re.split(r"[_/]", str(doc_id), maxsplit=1)[0]
                by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
            if meta.get("source_set") != ss:
                updates.append((row["chunk_id"], ss))

        result = {
            "table": table,
            "scanned": len(rows),
            "would_update": len(updates),
            "applied": 0,
            "distribution": stats,
            "unknown_prefixes": by_prefix,
        }
        if not apply or not updates:
            return result

        # 分批写，避免单条 UPDATE 挂太久（表可能是万行级）
        by_set: dict[str, list[str]] = {}
        for chunk_id, ss in updates:
            by_set.setdefault(ss, []).append(chunk_id)
        applied = 0
        for ss, ids in by_set.items():
            # jsonb 合并：只加/覆盖 source_set 一个键，其它键原样保留
            await conn.execute(
                f"""
                UPDATE {table}
                SET metadata = COALESCE(metadata, '{{}}'::jsonb)
                             || jsonb_build_object('source_set', $1::text)
                WHERE chunk_id = ANY($2::text[])
                """,
                ss,
                ids,
            )
            applied += len(ids)
        result["applied"] = applied
        return result
    finally:
        await conn.close()


async def _backfill_qdrant(kb_version: str | None, apply: bool) -> dict:
    """回填 Qdrant payload 的 source_set（dense 腿过滤的唯一依据）。"""
    from app.embedding.index_factory import create_index_client_from_settings

    client = await create_index_client_from_settings()
    collection = settings.rag_collection
    # 从 PG 拿 chunk_id → source_set 映射（Qdrant payload 里没有 source_doc_id 就够用）
    import asyncpg

    conn = await asyncpg.connect(_dsn())
    try:
        params, where = [], ""
        if kb_version:
            params.append(kb_version)
            where = "WHERE kb_version = $1"
        rows = await conn.fetch(
            f"SELECT chunk_id, source_doc_id, metadata FROM knowledge_chunks {where}", *params
        )
    finally:
        await conn.close()

    mapping: dict[str, str] = {}
    for row in rows:
        meta = row["metadata"]
        meta = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
        mapping[row["chunk_id"]] = source_set_of(
            source_doc_id=row["source_doc_id"], metadata=meta
        )

    result = {"backend": type(client).__name__, "collection": collection,
              "mapped": len(mapping), "applied_groups": 0}
    if not apply:
        # dry-run 不碰向量库（连接可能根本没建起来）
        result["would_apply"] = len(set(mapping.values()))
        return result

    setter = getattr(client, "set_payload", None)
    if setter is None:
        result["error"] = (
            "当前向量客户端没有 set_payload 方法 —— 回填向量 payload 需要先实现它；"
            "在此之前 dense 腿的 source_set filter 不会生效（BM25 腿与并列渲染不受影响）"
        )
        return result

    by_set: dict[str, list[str]] = {}
    for chunk_id, ss in mapping.items():
        by_set.setdefault(ss, []).append(chunk_id)
    for ss, ids in by_set.items():
        for i in range(0, len(ids), 500):
            await setter(collection, ids[i : i + 500], {"source_set": ss})
        result["applied_groups"] += 1
    return result


async def main_async(args) -> int:
    print(f"=== dry-run={not args.apply} kb_version={args.kb_version or '(全部)'} ===")
    for table in ("knowledge_chunks", "vector_index_knowledge_chunks"):
        try:
            stats = await _backfill_pg(args.kb_version, args.apply, table)
        except Exception as exc:  # 表可能不存在（某些部署没有 PGVector 表）
            print(f"[{table}] 跳过：{type(exc).__name__}: {str(exc)[:120]}")
            continue
        print(f"[{table}] {json.dumps(stats, ensure_ascii=False)}")
        if stats["unknown_prefixes"]:
            print(
                f"  ⚠️ 有 {sum(stats['unknown_prefixes'].values())} 条来源未识别，"
                f"按前缀分组：{stats['unknown_prefixes']}\n"
                f"     处置：若这些确实是新语料，往 "
                f"app/knowledge/source_set.py::SOURCE_SET_RULES 加一行前缀"
                f"（**不要**在图省事把它们硬塞进已有集合）。"
            )
    if args.qdrant:
        stats = await _backfill_qdrant(args.kb_version, args.apply)
        print(f"[qdrant] {json.dumps(stats, ensure_ascii=False)}")
    if not args.apply:
        print("\n以上为 dry-run。确认无误后加 --apply 真正写库。")
        print("⚠️ 回填 metadata 后**必须重建 BM25 索引**（重启主服务即重建）：")
        print("   `source_sets` 在建索引时算好并缓存，运行期改库不会自动生效。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="回填 source_set 到知识库与向量索引")
    ap.add_argument("--kb-version", default="", help="只处理该 kb_version（空=全部）")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    ap.add_argument("--qdrant", action="store_true", help="一并回填向量索引 payload")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
