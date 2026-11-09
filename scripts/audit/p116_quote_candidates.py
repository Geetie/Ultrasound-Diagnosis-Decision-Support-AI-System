#!/usr/bin/env python3
"""为 B2 扩充生成「KB 里真实存在的引文候选」。

## 为什么需要这个工具

B2 的 `gold[].evidence_quote` 必须**逐字**来自知识库 chunk（现有 152/152 命中）。
手写新条目时若先写问题、再去 KB 找引文，常常找不到逐字匹配的句子，
只能反复试错——这是扩充到 487 条时最大的时间浪费点。

本工具反过来：**先从 KB 里筛出「值得作为引文」的句子**（含数值/阈值/分档），
让人拿着候选句子写问题。这样每写一条必然命中。

## 用法

```bash
# 看某器官所有候选引文（按主题分类）
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p116_quote_candidates.py \
    --organ thyroid --kb-version kb_v2.1.0 --limit 60

# 按关键词收窄
... --keyword "4A" --keyword "15mm"
```

## 输出

每条候选：`chunk_id` / `source_set` / `quote`（已在 KB 中逐字存在）/ `why`（为什么值得提问）。
可直接作为 B2 条目的 `gold`。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: 「值得作为引文」的判据 —— 含可判定的医学事实，而不是泛泛叙述。
#: 每类给一个 why，写条目时可直接写进 notes。
_CANDIDATE_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("阈值", re.compile(r"[>＞≥≤<]\s*\d+\s*(?:mm|cm|%|分)"), "含明确阈值，可考『这个数是多少』"),
    ("区间", re.compile(r"\d+\s*[%~～]\s*\d+\s*%"), "含风险区间，可考『恶性率范围』"),
    ("分档", re.compile(r"([1-6])\s*(?:类|级)"), "含分类码，可考『积几分归几类』"),
    ("计分", re.compile(r"计\s*\d+\s*分|积\s*\d+\s*分|\+\s*1\s*分"), "含计分规则，可考『这项计几分』"),
    ("分级系统", re.compile(r"C-?TIRADS|LI-?RADS|TI-?RADS|TR\s?[1-5]|LR-?\s?[1-5M]"), "含分级体系码，可考『属哪一档』"),
    # 注意：**不要**用泛化的 `\d+ (mm|cm|%)` —— 它会把「术后切口感染率 1%~2%」
    # 这类**不是影像检索该考的内容**也收进来。只留影像判读相关的测量。
    ("结节大小", re.compile(r"(?:直径|大小|最大径|长径)\s*[>＞≥]?\s*\d+\s*(?:mm|cm)"),
     "含结节大小阈值，可考『多大要穿』"),
    ("随访", re.compile(r"随访|复查|间隔"), "含随访建议，可考『多久复查』"),
    ("穿刺", re.compile(r"穿刺|FNA|活检"), "含穿刺指征，可考『要不要穿』"),
)


#: 明显不是"指南原文句子"的行 —— 作为引文没有检索价值。
_NON_PROSE_RE = re.compile(
    r"^#{1,6}\s"              # Markdown 标题（多为本项目派生文档的伪标题）
    r"|^\|?[\s\-:|]+\|?$"      # 表格分隔行
    r"|^\s*\d+[\.、]\s*$"        # 纯编号行
)


def _sentences(text: str, *, min_len: int = 12, max_len: int = 120) -> list[str]:
    """把 chunk 切成候选句。

    **保留原文逐字**（只做首尾 strip，不改内部）——因为 evidence_quote 必须
    在 KB 里逐字命中，任何改写都会让新条目失效。

    同时过滤掉非散文行（Markdown 标题 / 表格分隔 / 纯编号）：它们虽然也在
    chunk 里，但作为"证据引文"没有检索价值，会把候选列表灌满噪声。
    """
    out: list[str] = []
    for raw in re.split(r"[。！？；\n]", text or ""):
        s = raw.strip()
        if not (min_len <= len(s) <= max_len):
            continue
        if _NON_PROSE_RE.match(s):
            continue
        if not re.search(r"[\u4e00-\u9fff]", s):   # 没中文 → 英文参考文献/地址
            continue
        out.append(s)
    return out


def classify(sentence: str) -> tuple[str, str] | None:
    """判断这句话属于哪类候选；不属于任何类则返回 None。"""
    hits = [(name, why) for name, pat, why in _CANDIDATE_PATTERNS if pat.search(sentence)]
    if not hits:
        return None
    # 多类命中时取第一个（阈值 > 区间 > 分档 > …，顺序即优先级）
    return hits[0]


async def collect(kb_version: str, organ: str | None) -> list[dict]:
    from app.config.database import AsyncSessionLocal
    from app.knowledge.source_set import source_set_of
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        sql = "SELECT chunk_id, organ, content, source_doc_id, metadata FROM knowledge_chunks WHERE kb_version = :v"
        params: dict = {"v": kb_version}
        if organ:
            sql += " AND organ = :o"
            params["o"] = organ
        rows = (await s.execute(text(sql), params)).fetchall()

    out: list[dict] = []
    for cid, org, content, doc_id, meta in rows:
        ss = source_set_of(source_doc_id=doc_id, metadata=meta)
        for sent in _sentences(content or ""):
            c = classify(sent)
            if c is None:
                continue
            out.append({
                "chunk_id": cid,
                "organ": org,
                "source_set": ss,
                "quote": sent,
                "kind": c[0],
                "why": c[1],
            })
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description="B2 引文候选生成（p116）")
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--organ", default=None, help="thyroid / liver / cardiac；不传=全部")
    ap.add_argument("--keyword", action="append", default=[], help="收窄关键词，可多次传")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true", help="输出 JSON（可直接喂给写条目流程）")
    args = ap.parse_args()

    cands = await collect(args.kb_version, args.organ)
    if args.keyword:
        cands = [c for c in cands if all(k in c["quote"] for k in args.keyword)]

    # 去重（同一句话可能在多个版本 chunk 里出现）
    seen: set[str] = set()
    uniq: list[dict] = []
    for c in cands:
        key = c["quote"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    print(f"=== 候选引文（kb={args.kb_version}, organ={args.organ or 'all'}, "
          f"共 {len(uniq)} 条，展示前 {min(args.limit, len(uniq))}）===\n")
    for i, c in enumerate(uniq[: args.limit], 1):
        if args.json:
            print(json.dumps(c, ensure_ascii=False))
        else:
            print(f"{i:3d}. [{c['source_set']:24s}] {c['kind']}")
            print(f"     quote: {c['quote']}")
            print(f"     chunk: {c['chunk_id']}")
            print(f"     why  : {c['why']}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
