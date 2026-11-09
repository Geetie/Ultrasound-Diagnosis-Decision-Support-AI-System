#!/usr/bin/env python3
"""L3 人工精标：证明 L2 合成集可信（目标交付项 1 第三层）。

## L3 的作用不是"替代 L2"，是"证明 L2 可信"

Ragas 与业界共识：合成集必须有**人工抽检**证明其质量，
否则合成出来的东西可能整批是废的而没人知道。

## L3 与 L2 的差异

| | L2 合成 | **L3 精标** |
|---|---|---|
| 来源 | LLM 按知识图生成 | **人工逐条写** |
| 数量 | 480+ 条 | **50-100 条** |
| 用途 | 覆盖矩阵评测 | **交叉验证 L2 是否可信** |
| 判定 | 七道自动闸 | 人工判断临床合理性 |

## 交叉验证怎么做（这是关键）

L3 的作用是回答：**L2 合成的数据，质量和人工精标的差多少？**

做法：
1. 人工精标 50-100 条（同一套 knowledge graph 的节点，但 query 人手写）
2. 在同一批 L2 上跑同样的人工判定标准（抽 50-100 条）
3. 对比：
   - 临床合理性通过率
   - 答案忠实度（是否只依据原文）
   - query 自然度
4. **如果两者差距 <10pt，说明 L2 可信**

## 本脚本做什么

生成**配对抽样清单**：从 L2 里按格子分层抽 N 条，
输出待人工审的评审表（含判定标准与打分格），供人工填写。

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p127_l3_manual_review.py \
    --l2 /tmp/synth_full.jsonl --out /tmp/l3_review.md --sample 60
```
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DIMS = ("hop", "specificity", "persona", "length")

_TEMPLATE = """# L3 人工评审表（用于证明 L2 合成集可信）

> 生成时间：{ts}
> 抽样：{n} 条（按 hop×specificity×persona×length 分层）
> **目的：证明 L2 合成的数据在临床合理性与答案忠实度上接近人工水平**

## 评审标准（每条打 3 个分）

| 维度 | 3 分（好） | 2 分（可接受） | 1 分（差） |
|---|---|---|---|
| **临床合理性** | 医生真的会这样问 | 略生硬但能懂 | 医生不会这样问 |
| **答案忠实度** | 完全依据原文，无补充 | 基本依据，少量常识 | 有明显编造/外推 |
| **问法自然度** | 像真人提问 | 略有模板感 | 明显机器味 |

**注意**：答案忠实度要回去查原文（`gold` 里的 chunk_id）。
不许凭印象打分。

---

## 待审条目

"""

_ITEM = """### {i}. {sid}

**问题**：{query}

**参考答案**：{answer}

**依据 chunk**：`{gold}`

**元数据**：{meta}

| 维度 | 分 | 备注 |
|---|---|---|
| 临床合理性 | | |
| 答案忠实度 | | |
| 问法自然度 | | |

---

"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 L3 人工评审表（p127）")
    ap.add_argument("--l2", required=True)
    ap.add_argument("--out", default="/tmp/l3_review.md")
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    rows = [
        json.loads(l)
        for l in Path(args.l2).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    ok = [r for r in rows if r.get("gate_passed")]
    print(f"L2 总 {len(rows)} 条，过闸 {len(ok)} 条")

    # 按格子分层抽
    rnd = random.Random(args.seed)
    cells: dict[tuple, list] = defaultdict(list)
    for r in ok:
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
        key = tuple(str(md.get(d, "?")) for d in DIMS)
        cells[key].append(r)

    per_cell = max(1, args.sample // max(len(cells), 1))
    picked: list[dict] = []
    for k, items in sorted(cells.items()):
        rnd.shuffle(items)
        picked.extend(items[:per_cell])
    rnd.shuffle(picked)
    picked = picked[: args.sample]

    print(f"分层抽样：{len(cells)} 格 → 抽 {len(picked)} 条")

    from datetime import datetime, timezone

    md_out = _TEMPLATE.format(
        ts=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        n=len(picked),
    )
    for i, r in enumerate(picked, 1):
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
        gold = ", ".join(g.get("chunk_id", "") for g in (r.get("gold") or []))
        meta = " · ".join(f"{d}={md.get(d, '?')}" for d in DIMS) + f" · organ={r.get('organ', '?')}"
        md_out += _ITEM.format(
            i=i,
            sid=r.get("scenario_id", "?"),
            query=r.get("query", ""),
            answer=r.get("reference_answer", ""),
            gold=gold,
            meta=meta,
        )

    md_out += """
---

## 统计（填完后算）

```
临床合理性   平均 __ / 3
答案忠实度   平均 __ / 3
问法自然度   平均 __ / 3

vs L2 自动闸判定：
  七道闸通过率 = __%
  人工"临床合理性 ≥2"通过率 = __%
  差距 = __pt   →  <10pt 认为 L2 可信
```
"""

    out = Path(args.out)
    out.write_text(md_out, encoding="utf-8")
    print(f"写出评审表 → {out} ({out.stat().st_size} 字节)")

    # 同时导出 JSON 便于后续统计
    json_out = out.with_suffix(".jsonl")
    with json_out.open("w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps({
                "l3_id": f"l3-{picked.index(r)+1:03d}",
                "scenario_id": r.get("scenario_id"),
                "query": r.get("query"),
                "reference_answer": r.get("reference_answer"),
                "gold": r.get("gold"),
                "Organ": r.get("organ"),
                "scores": None,   # 人工填
            }, ensure_ascii=False) + "\n")
    print(f"写出评分载体 → {json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
