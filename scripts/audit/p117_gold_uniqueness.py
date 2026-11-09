#!/usr/bin/env python3
"""校验 B2 gold 的「引文唯一性」——找出辨识度不足的标注。

## 为什么需要这个工具

2026-11-09 实测：我把 B2 从 120 扩到 167 条后，hit@1 从 0.5556 掉到 0.1064。
查清后是两个标注问题：

1. **gold 标成单条**（已用脚本补齐）
2. **引文太短**：旧条目引文 P50 = 23 字，我新写的 P50 = **13 字**，
   `<15 字` 占比从 27.1% 涨到 **59.5%**。

引文越短，在 KB 里出现的 chunk 越多 → 它在检索任务里"指代"的能力越弱。
极端例子：`gold = ["微钙化"]`（3 字）—— 几乎所有甲状腺 chunk 都有这个词，
标它等于没标。

## 判据

| 检查 | 阈值 | 说明 |
|---|---|---|
| 引文长度 | ≥15 字 | 太短的片段不具辨识度 |
| 命中 chunk 数 | ≤5 个 | 超过说明这句在库里太泛 |

两类都不合格的，建议**重写该条**（或至少不要把它当作唯一 gold）。

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p117_gold_uniqueness.py \
    --dataset data/eval/v2/zh_retrieval_v2_candidate.jsonl --kb-version kb_v2.1.0
```

退出码：0 = 全过；1 = 有不合格项（可作为 CI 门禁）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

MIN_QUOTE_CHARS = 15
MAX_CHUNKS_PER_QUOTE = 5


async def check(dataset: Path, kb_version: str) -> tuple[list[dict], int]:
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    rows = [json.loads(ln) for ln in dataset.read_text(encoding="utf-8").splitlines() if ln.strip()]

    async with AsyncSessionLocal() as s:
        corpus = (await s.execute(
            text("SELECT chunk_id, content FROM knowledge_chunks WHERE kb_version=:v"),
            {"v": kb_version},
        )).fetchall()
    print(f"KB({kb_version}) chunk 数: {len(corpus)}；数据集 {len(rows)} 条\n")

    problems: list[dict] = []
    for e in rows:
        for gi, g in enumerate(e.get("gold") or []):
            q = (g.get("evidence_quote") or "").strip()
            if not q:
                problems.append({"query_id": e["query_id"], "kind": "空引文"})
                continue
            n_hit = sum(1 for _cid, c in corpus if q in (c or ""))

            reasons = []
            if len(q) < MIN_QUOTE_CHARS:
                reasons.append(f"引文仅 {len(q)} 字(<{MIN_QUOTE_CHARS})")
            if n_hit > MAX_CHUNKS_PER_QUOTE:
                reasons.append(f"命中 {n_hit} 个 chunk(>{MAX_CHUNKS_PER_QUOTE})")
            if n_hit == 0:
                reasons.append("KB 里 0 命中（标错 chunk 或换行差异）")

            if reasons:
                problems.append({
                    "query_id": e["query_id"],
                    "gold_index": gi,
                    "quote": q[:50],
                    "n_chunks": n_hit,
                    "reasons": reasons,
                })
    return problems, len(rows)


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B2 gold 引文唯一性校验（p117）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--limit", type=int, default=40, help="展示前 N 个问题项")
    args = ap.parse_args(argv)

    problems, n = await check(Path(args.dataset), args.kb_version)

    bad_qids = {p["query_id"] for p in problems}
    print(f"=== 结果 ===")
    print(f"  条目总数: {n}")
    print(f"  有问题的条目: {len(bad_qids)}  ({len(bad_qids)/max(n,1)*100:.1f}%)")
    print(f"  问题 gold 数: {len(problems)}\n")

    for p in problems[: args.limit]:
        print(f"  {p['query_id']} [gold#{p.get('gold_index','-')}] "
              f"命中{p.get('n_chunks','?')}块 | {'; '.join(p['reasons'])}")
        if p.get("quote"):
            print(f"      {p['quote']!r}")

    if len(problems) > args.limit:
        print(f"  … 还有 {len(problems) - args.limit} 项")

    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
