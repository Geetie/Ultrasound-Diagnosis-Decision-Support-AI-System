#!/usr/bin/env python3
"""把现有 B2（487 条）升级为 Ragas 口径的评测集（目标交付项的最短路径）。

## 为什么这条路对（15 轮合成的结论）

合成的根本障碍是 **II-Medical-8B 不知道医学事实**：
- 「APHE 分 6 级」（实际是二值）
- 「4 类以上恶性可能逐渐减小」（方向反了）
- 摘录时选不对段落（抄得真实但与问题无关）

**但 B2 里的 `evidence_quote` 已经解决了这个问题** ——
它是**人工从 KB 逐字摘录**的，且已通过 p117 三判据（487 条 0 问题）。

## 升级要做的三件事

### 1. `reference_answer` = 把 gold 里的 evidence_quote 拼起来

**这不是"生成"，是"拼接已有的人工摘录"。**

```
gold[0].evidence_quote: 实性、 微钙化、 极低回声…是可疑恶性超声特征
gold[1].evidence_quote: |1|2~10|4A<sup>低度可疑恶性</sup>|
→ reference_answer: 原文提到：实性、微钙化、极低回声…是可疑恶性超声特征。
                     原文提到：|1|2~10|4A（低度可疑恶性）|
```

**为什么可信**：每段都是人写进 gold 的、已过 p117 的逐字原文。

### 2. 四维 metadata —— **必须诚实标注**

**关键限制**：B2 的 query 是人手写的问句，**没有原始场景信息**。
所以 `hop` / `specificity` / `length` / `persona` **不能凭空填**，只能：

| 维度 | 怎么填 | 依据 |
|---|---|---|
| `length` | 按 query 字数机械判定 | 客观可算 |
| `hop` | gold 条数 ≥2 → multi，否则 single | **代理指标**，不是真 hop |
| `specificity` | 按 query 是否含具体数值/分级码判定 | 客观可算 |
| `persona` | **标注为 `unassigned`** | **不能编**——原数据没有这个信息 |
| `source` | `"manual_b2"` | 真实来源 |
| `dataset_version` | `rag-eval-v2.0.0-b2upgrade` | — |

**`persona=unassigned` 是诚实的**：Ragas 要求这个维度，但 B2 没有，
编一个会让「按 persona 切片分析」变成假分析。

### 3. 覆盖矩阵检查会显示 persona 维度全缺 —— **这是真实情况**

交付项 3 要求「每格 ≥10」。在 persona 未标注的情况下，
矩阵只有 2×2×1×3 = 12 格，不是 48 格。**如实报告。**

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p128_b2_upgrade.py \
    --in data/eval/v2/zh_retrieval_v2_candidate.jsonl \
    --out data/eval/v3/zh_retrieval_v3.jsonl
```
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DATASET_VERSION = "rag-eval-v2.0.0-b2upgrade"

#: 具体性判据：含明确数值或分级码 → specific
_SPECIFIC_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|分)"
    r"|(?:C-?TIRADS|TI-?RADS|LI-?RADS|CEUS|LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-Ca-c]?\s*类)",
    re.I,
)


def _clean_quote(q: str) -> str:
    """清理 evidence_quote 里的 KB 排版噪声（`<sup>`、全角空格等）。

    **只清排版，不改内容** —— 保留每个字，否则就变成"改写"了。
    """
    t = q or ""
    t = re.sub(r"</?sup>", "", t)          # <sup> 标签
    t = re.sub(r"<[^>]{1,20}>", "", t)     # 其它标签
    t = re.sub(r"[ \t]+", " ", t)          # 连续空白
    t = t.replace("\u3000", " ")           # 全角空格
    return t.strip()


def build_reference_answer(gold: list[dict]) -> tuple[str, list[str]]:
    """把 gold 的 evidence_quote 拼成 reference_answer。

    Returns:
        ``(拼接后的答案, 问题列表)``
    """
    probs: list[str] = []
    quotes = [_clean_quote(g.get("evidence_quote") or "") for g in (gold or [])]
    quotes = [q for q in quotes if len(q) >= 8]
    if not quotes:
        return "", ["gold 里没有可用的 evidence_quote"]

    # 逐段加「原文提到：」前缀，段间用句号分隔
    parts = [f"原文提到：{q.rstrip('。.；;')}" for q in quotes]
    ans = "。".join(parts) + "。"
    if len(ans) < 40:
        probs.append(f"拼接后仍过短 {len(ans)}<40")
    return ans, probs


def infer_metadata(row: dict) -> dict:
    """从 query 与 gold 推导四维 metadata。

    **只填能从数据里客观算出来的，算不出的标 unassigned。**
    """
    q = row.get("query") or ""
    gold = row.get("gold") or []

    # length：按字数机械判定
    n = len(q)
    length = "short" if n <= 25 else ("medium" if n <= 45 else "long")

    # hop：gold 条数代理（≥2 → multi）
    hop = "multi" if len(gold) >= 2 else "single"

    # specificity：query 是否含具体数值/分级码
    specificity = "specific" if _SPECIFIC_RE.search(q) else "abstract"

    return {
        "organ": row.get("organ"),
        "persona": "unassigned",   # **不编**：原数据无此信息
        "hop": hop,
        "specificity": specificity,
        "length": length,
        "source": "manual_b2",
        "dataset_version": DATASET_VERSION,
        "query_type": row.get("query_type"),
        "hop_note": "由 gold 条数代理，非真实推理跳数",
        "persona_note": "原数据无 persona 信息，标 unassigned 不伪造",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B2 → Ragas 口径升级（p128）")
    ap.add_argument("--in", dest="inp", default="data/eval/v2/zh_retrieval_v2_candidate.jsonl")
    ap.add_argument("--out", default="data/eval/v3/zh_retrieval_v3.jsonl")
    args = ap.parse_args(argv)

    src = Path(args.inp)
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"读入 {len(rows)} 条")

    out_rows = []
    n_ans = 0
    probs_hist: dict[str, int] = {}
    for r in rows:
        ans, probs = build_reference_answer(r.get("gold") or [])
        meta = infer_metadata(r)
        if ans:
            n_ans += 1
        for p in probs:
            k = p.split(" ")[0]
            probs_hist[k] = probs_hist.get(k, 0) + 1

        out_rows.append(
            {
                "id": r.get("query_id"),
                "query": r.get("query"),
                "reference_answer": ans,
                "gold": [
                    {
                        "chunk_id": f"kb_v2.1.0:{g.get('doc_id')}#{int(g.get('chunk_index', 0)):04d}",
                        "doc_id": g.get("doc_id"),
                        "chunk_index": g.get("chunk_index"),
                        "evidence_quote": g.get("evidence_quote"),
                    }
                    for g in (r.get("gold") or [])
                ],
                "answerable": r.get("answerable", True),
                "imaging_terms": r.get("imaging_terms") or [],
                "notes": r.get("notes"),
                "metadata": meta,
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n=== 结果 ===")
    print(f"  有 reference_answer: {n_ans}/{len(out_rows)}")
    if probs_hist:
        print(f"  问题分布: {probs_hist}")

    from collections import Counter

    print(f"\n=== metadata 分布 ===")
    for dim in ("organ", "persona", "hop", "specificity", "length", "source"):
        c = Counter(r["metadata"][dim] for r in out_rows)
        print(f"  {dim:12s}: {dict(c.most_common())}")

    cells = Counter(
        (r["metadata"]["hop"], r["metadata"]["specificity"],
         r["metadata"]["persona"], r["metadata"]["length"])
        for r in out_rows
    )
    print(f"\n=== 覆盖矩阵 ===")
    print(f"  实际格子: {len(cells)} / 理论 24（2×2×1×3，persona 只有 unassigned）")
    short = {k: v for k, v in cells.items() if v < 10}
    print(f"  达到 ≥10 的格子: {len(cells) - len(short)}/{len(cells)}")

    print(f"\n写出 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
