"""找一份可签报告（供交付门禁使用），并把 id 写进 /tmp/_rid_env.sh。

为什么写文件而不是打到 stdout 让 shell eval：调试时被 CRLF + grep + eval
三重坑过（值明明在，eval 取不到）。直接产出可 source 的文件最稳。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, os.path.expanduser("~/uai/src"))
from sqlalchemy import text

from app.config.database import AsyncSessionLocal

DRAFT_STATUSES = ("draft", "pending", "generated")


async def main() -> int:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "select report_id, status, request_id from diagnosis_reports "
            "order by id desc limit 5"
        ))).all()
    if not rows:
        print("NO_REPORTS")
        return 2
    for r in rows:
        print(f"cand={r[0]} status={r[1]} request={r[2]}")
    draft = [r for r in rows if r[1] in DRAFT_STATUSES]
    picked = (draft or rows)[0][0]
    pathlib.Path("/tmp/_rid_env.sh").write_text(f"RID={picked}\n", encoding="utf-8")
    print(f"RID={picked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
