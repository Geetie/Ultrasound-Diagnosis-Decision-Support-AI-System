"""回填存量知识库的图谱边（graph_query.py 边契约）。

对指定 kb_version 的 knowledge_chunks，按 (source_doc_id, chunk_index) 排序，
为每块的 metadata.relations 写入同文档相邻块互指边（next_section/prev_section）。
只操作 metadata JSONB，不动向量——无需重灌 vector_index。

用法（服务器）:
    ~/uai/venv/bin/python scripts/kb_backfill_relations.py \
        --kb-version kb_v1.3.0 [--dry-run]

幂等：metadata 已含 relations 的块跳过。

（A.63 WP1：补仓库根自举 —— 原先必须外部带 PYTHONPATH，否则报
`ModuleNotFoundError: No module named 'app'`；与同目录其它脚本的自举写法对齐。）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from sqlalchemy import text

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def backfill(kb_version: str, dry_run: bool = False) -> int:
    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT chunk_id, source_doc_id, metadata
                    FROM knowledge_chunks
                    WHERE kb_version = :v
                    ORDER BY source_doc_id, (metadata->>'chunk_index')::int
                    """
                ),
                {"v": kb_version},
            )
        ).fetchall()

        by_doc: dict[str, list[dict]] = {}
        for row in rows:
            m = row._mapping
            by_doc.setdefault(str(m["source_doc_id"]), []).append(
                {"chunk_id": str(m["chunk_id"]), "metadata": m["metadata"] or {}}
            )

        updates: list[tuple[str, list[dict[str, str]]]] = []
        for _doc_id, chunks in by_doc.items():
            ids = [c["chunk_id"] for c in chunks]
            for i, c in enumerate(chunks):
                if "relations" in (c["metadata"] or {}):
                    continue  # 幂等：已有边不覆盖
                relations: list[dict[str, str]] = []
                if i + 1 < len(ids):
                    relations.append(
                        {"target": ids[i + 1], "relation": "next_section"}
                    )
                if i > 0:
                    relations.append(
                        {"target": ids[i - 1], "relation": "prev_section"}
                    )
                if relations:
                    updates.append((c["chunk_id"], relations))

        print(
            f"[backfill] kb_version={kb_version} chunks={len(rows)} "
            f"docs={len(by_doc)} to_update={len(updates)} dry_run={dry_run}"
        )
        if dry_run:
            for cid, rel in updates[:5]:
                print("  sample:", cid, json.dumps(rel, ensure_ascii=False))
            return 0

        for cid, rel in updates:
            await session.execute(
                text(
                    """
                    UPDATE knowledge_chunks
                    SET metadata = metadata || CAST(:rel AS jsonb)
                    WHERE chunk_id = :cid
                    """
                ),
                {"cid": cid, "rel": json.dumps({"relations": rel})},
            )
        await session.commit()
        print(f"[backfill] committed {len(updates)} metadata updates")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-version", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(backfill(args.kb_version, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
