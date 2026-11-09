"""临时探针：导出 kb_v2.1.0 全量 chunk 供本地逐字取引文。用完即删。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SRC = Path.home() / "uai" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUT = Path.home() / "uai" / "data" / "eval" / "_kb_dump_kb210.json"


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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("chunks:", len(out), "->", OUT)
    return 0


sys.exit(asyncio.run(main()))
