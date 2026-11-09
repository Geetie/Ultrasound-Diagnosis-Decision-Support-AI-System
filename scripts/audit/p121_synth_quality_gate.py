#!/usr/bin/env python3
"""合成数据质量门（p121）：不靠模型自觉，靠程序判据。

## 为什么必须有这把闸

实测：即使提示词明确写「禁止编造文献名称与年份」，II-Medical-8B 仍然输出：
- `依据《C-TIRADS 2018》` ← 真实是 2021
- `根据《LI-RADS 2017》` ← 用在了甲状腺问题上
- `根据《Hürthle 细胞肿瘤》` ← 编的

**8B 模型约束不住。必须程序校验。**

## 五道闸

| 闸 | 判据 | 抓什么问题 |
|---|---|---|
| 1 引文真实性 | 答案里的 `《...》` 必须能在 KB 里找到同名字符串 | 编造文献名 |
| 2 体系一致性 | 甲状腺问题不许出现 LI-RADS；肝脏不许出现 C-TIRADS | 跨体系混用 |
| 3 问句形态 | query 必须含问号或疑问词，且 ≤80 字 | 把原文复述当 query |
| 4 答案信息量 | reference_answer ≥40 字，且不能整段抄 query | 空洞答案 |
| 5 gold 一致性 | gold 的 chunk_id 必须存在，且非空 | 标错位置 |

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p121_synth_quality_gate.py \
    --dataset /tmp/synth.jsonl --kb-version kb_v2.1.0
```
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

_CITE_RE = re.compile(r"《([^》]{2,40})》")
_ASK_RE = re.compile(r"[？?]|要不要|是否需要|怎么|如何|为什么|什么|多少|是否|能否|该不该|下一步")

#: 器官 → 允许出现的分级体系；出现其它体系 = 跨体系混用
_ORGAN_SYSTEMS = {
    "thyroid": ("C-TIRADS", "CTIRADS", "TI-RADS", "TIRADS", "TR1", "TR2", "TR3", "TR4", "TR5"),
    "liver": ("LI-RADS", "LIRADS", "LR-", "CEUS", "OPTN"),
    "cardiac": ("LVEF", "EF", "E/e", "GLS", "ASE", "BSE"),
}
#: 明确不该出现的（跨器官体系）
_FORBIDDEN = {
    "thyroid": ("LI-RADS", "LIRADS", "LR-1", "LR-2", "LR-3", "LR-4", "LR-5", "LR-M"),
    "liver": ("C-TIRADS", "CTIRADS", "TI-RADS"),
    "cardiac": ("C-TIRADS", "LI-RADS", "TI-RADS"),
}

MIN_ANSWER = 40
MAX_QUERY = 80


async def load_kb_texts(kb_version: str) -> str:
    """把 KB 全文拼起来，用于查「引文里的书名是否真实存在」。"""
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                text("SELECT chunk_id, content FROM knowledge_chunks WHERE kb_version = :v"),
                {"v": kb_version},
            )
        ).fetchall()
    return "\n".join((c or "") for _cid, c in rows)


def check_one(row: dict, kb_text: str, existing_ids: set[str]) -> list[str]:
    """返回问题列表（空 = 通过）。"""
    probs: list[str] = []
    organ = row.get("organ") or ""
    q = (row.get("query") or "").strip()
    a = (row.get("reference_answer") or "").strip()

    # ---- 闸 3：问句形态
    if not q:
        probs.append("query 为空")
    else:
        if not _ASK_RE.search(q):
            probs.append("query 不含疑问词（像陈述而非提问）")
        if len(q) > MAX_QUERY:
            probs.append(f"query 过长 {len(q)}>{MAX_QUERY}（像复述原文）")

    # ---- 闸 4：答案信息量
    if len(a) < MIN_ANSWER:
        probs.append(f"答案过短 {len(a)}<{MIN_ANSWER}")
    if q and a and q in a:
        probs.append("答案整段抄了 query")

    # ---- 闸 1：引文真实性（《X》必须在 KB 里出现过）
    for m in _CITE_RE.finditer(a + " " + q):
        name = m.group(1).strip()
        # 允许的形式：KB 里含该名字，或含其去掉空格/连字符的变体
        variants = {name, name.replace(" ", ""), name.replace("-", ""), name.upper(), name.lower()}
        if not any(v and v in kb_text for v in variants):
            probs.append(f"编造文献名《{name}》（KB 里找不到）")
            break   # 一条就够，不必刷屏

    # ---- 闸 2：体系一致性
    text = q + " " + a
    for bad in _FORBIDDEN.get(organ, ()):
        if bad.lower() in text.lower():
            probs.append(f"{organ} 问题出现「{bad}」（跨体系混用）")
            break

    # ---- 闸 6（2026-11-09 新增）：query 里的体系码必须与器官匹配
    #
    # 实测漏网案例：
    #   [liver] 「报告写 TR4，我作为外科医生该不该安排手术？」← TR4 是甲状腺的码
    #   [liver] 「肝脏 13mm 4A 类结节紧邻被膜，要不要穿刺？」← 4A 是甲状腺的码
    #
    # 原闸 2 只查 `_FORBIDDEN` 里的**体系名**（C-TIRADS / LI-RADS），
    # 没查**裸码**（TR4 / 4A / LR-5）。而模型恰恰爱写裸码。
    _BARE_CODE_OWNER = {
        "thyroid": ("TR1", "TR2", "TR3", "TR4", "TR5", "4A", "4B", "4C"),
        "liver": ("LR-1", "LR-2", "LR-3", "LR-4", "LR-5", "LR-M", "LR-TIV"),
        "cardiac": (),
    }
    q_upper = (q or "").upper()
    for other_organ, codes in _BARE_CODE_OWNER.items():
        if other_organ == organ:
            continue
        for code in codes:
            # 只匹配「独立出现的码」，避免 4A 命中 LABEL 之类
            if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![0-9A-Za-z])", q_upper):
                probs.append(
                    f"{organ} 问题的 query 里出现「{code}」（属于 {other_organ} 的码）"
                )
                break
        else:
            continue
        break

    # ---- 闸 7（2026-11-09 新增）：答案里不许有「原文没有的具体数值」
    #
    # 实测漏网案例：「C-TIRADS 3 类…一般2~3个月复检一次彩超」
    # —— 原文说的是「随访间隔 6~24 个月」，2~3 个月是编的。
    #
    # 判据：答案里出现的**带单位数值**，若在原文（KB 全文）里找不到，
    # 且不是常见的通用值（如 1mm 这类），就报警。
    # 这是启发式：可能误伤"合理换算"（如 1cm 写成 10mm），所以只作 WARN 级。
    _num_re = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁)")
    invented: list[str] = []
    for m in _num_re.finditer(a):
        tok = m.group(0).replace(" ", "")
        # 允许等效写法：1cm ≈ 10mm
        variants = {tok, tok.replace("cm", "mm"), tok.replace("mm", "cm")}
        if not any(v in kb_text for v in variants):
            invented.append(tok)
    if invented:
        probs.append(f"答案含原文没有的数值: {invented[:3]}")

    # ---- 闸 5：gold 一致性
    gold = row.get("gold") or []
    if not gold:
        probs.append("gold 为空")
    else:
        for g in gold:
            cid = g.get("chunk_id") or ""
            if cid and cid not in existing_ids:
                probs.append(f"gold chunk 不存在: {cid[:50]}")
                break

    return probs


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="合成数据质量门（p121）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--limit", type=int, default=20, help="展示前 N 个问题项")
    args = ap.parse_args(argv)

    rows = [
        json.loads(l)
        for l in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    rows = [r for r in rows if "error" not in r]

    kb_text = await load_kb_texts(args.kb_version)
    print(f"数据集 {len(rows)} 条；KB 全文 {len(kb_text)} 字")

    # gold chunk id 集合（用于闸 5）
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        ids = {
            r[0]
            for r in (
                await s.execute(
                    text("SELECT chunk_id FROM knowledge_chunks WHERE kb_version = :v"),
                    {"v": args.kb_version},
                )
            ).fetchall()
        }

    gates = {"闸1 引文真实": 0, "闸2 体系一致": 0, "闸3 问句形态": 0, "闸4 答案信息量": 0, "闸5 gold一致": 0}
    bad_rows: list[tuple[str, list[str]]] = []
    for r in rows:
        probs = check_one(r, kb_text, ids)
        if probs:
            bad_rows.append((r.get("scenario_id") or r.get("query", "")[:20], probs))
            for p in probs:
                if "编造文献名" in p:
                    gates["闸1 引文真实"] += 1
                elif "跨体系" in p:
                    gates["闸2 体系一致"] += 1
                elif "query" in p:
                    gates["闸3 问句形态"] += 1
                elif "答案" in p:
                    gates["闸4 答案信息量"] += 1
                elif "gold" in p:
                    gates["闸5 gold一致"] += 1

    print(f"\n=== 五道闸 ===")
    for k, v in gates.items():
        print(f"  {k}: {v} 条不合规")
    n_bad = len(bad_rows)
    print(f"\n总不合规条目: {n_bad}/{len(rows)} = {n_bad/max(len(rows),1)*100:.1f}%")

    for sid, probs in bad_rows[: args.limit]:
        print(f"\n  {sid}:")
        for p in probs:
            print(f"    - {p}")
    if n_bad > args.limit:
        print(f"\n  … 还有 {n_bad - args.limit} 条")

    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
