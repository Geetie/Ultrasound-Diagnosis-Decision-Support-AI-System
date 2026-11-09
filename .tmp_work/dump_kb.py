"""一次性探针：把 kb_v2.1.0 的 chunk 全量导出为 JSON，供本地手写条目时逐字取引文。

用完即删（AGENTS.md 硬规则 11）。
"""
from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, "/mnt/ee16/home/student03/uai/src")


async def main() -> int:
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            text(
                "SELECT chunk_id, organ, content, source_doc_id, metadata "
                "FROM knowledge_chunks WHERE kb_version=:v ORDER BY chunk_id"
            ),
            {"v": "kb_v2.1.0"},
        )).fetchall()

    out = [
        {"chunk_id": cid, "organ": org, "content": content or "",
         "doc_id": doc, "metadata": meta}
        for cid, org, content, doc, meta in rows
    ]
    path = "/mnt/ee16/home/student03/uai/data/eval/_kb_dump_kb210.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("chunks:", len(out), "->", path)
    return 0


raise SystemExit(asyncio.run(main()))
