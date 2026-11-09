#!/usr/bin/env python3
"""给 L1 的 ask 类条目自动预标 gold（**带置信度，供人工复核**）。

## 核心矛盾：自动标 gold 会循环论证

用检索的 top-1 当 gold，再去测同一检索系统 → 必然 100% 命中，毫无意义。

**所以本脚本不做"自动标 gold"，而是做"候选排序 + 置信度评估"**：

对每条 L1 的 top-5 候选，用**独立于检索的判据**评估「这块能不能回答该问题」：

| 判据 | 说明 | 独立性 |
|---|---|---|
| 数值/术语覆盖 | 问题提到的分级码/阈值，是否出现在候选里 | 与 BM25 词重叠不同源 |
| 答案完整性 | 候选是否含「建议/推荐/阈值/分类」这类结论词 | 独立 |
| 长度适配 | 过短（<100 字）通常答不全 | 独立 |

输出 `auto_gold` + `confidence`，**只有 confidence=high 的才建议直接采用**，
其余标 `needs_human_annotation`。

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p125_autolabel_l1.py \
    --in /tmp/l1_zh.jsonl --out /tmp/l1_labeled.jsonl
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

#: 结论词（候选含这些说明它有"答案"而不只是描述）
_CONCLUSION = (
    "建议", "推荐", "应", "需", "阈值", "指征", "分类", "属于", "归为",
    "恶性率", "风险", "随访", "穿刺", "手术", "消融", "mm", "cm", "%",
)

#: 关键锚点（分级码 / 数值阈值）
_ANCHOR = (
    re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月)", re.IGNORECASE),
    re.compile(r"(?:C-?TIRADS|TI-?RADS|LI-?RADS|LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-C]?\s*类)", re.IGNORECASE),
)


def _question_anchors(intent: str) -> set[str]:
    """从意图里抽「这个问题在问什么」的锚点。"""
    out: set[str] = set()
    for pat in _ANCHOR:
        for m in pat.finditer(intent or ""):
            out.add(m.group(0).replace(" ", "").lower())
    return out


def score_candidate(intent: str, cand: dict) -> tuple[int, list[str]]:
    """给候选打分（分越高越可能是正确答案）。返回 (分数, 理由)。"""
    txt = cand.get("preview") or ""
    score = 0
    why: list[str] = []

    # 1) 问题锚点被覆盖
    qa = _question_anchors(intent)
    if qa:
        hit = [a for a in qa if a in txt.replace(" ", "").lower()]
        if hit:
            score += 3 * len(hit) // max(len(qa), 1) + 1
            why.append(f"覆盖问题锚点 {len(hit)}/{len(qa)}")

    # 2) 含结论词
    concl = [w for w in _CONCLUSION if w in txt]
    if concl:
        score += min(len(concl), 3)
        why.append(f"含结论词×{len(concl)}")

    # 3) 长度适配（preview 只有 150 字，所以 >=100 说明正文够长）
    if len(txt) >= 140:
        score += 1
        why.append("长度充足")

    return score, why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1 自动预标 gold（p125）")
    ap.add_argument("--in", dest="inp", default="/tmp/l1_zh.jsonl")
    ap.add_argument("--out", default="/tmp/l1_labeled.jsonl")
    ap.add_argument("--high-threshold", type=int, default=5,
                    help="自动采用为 gold 的最低分")
    args = ap.parse_args(argv)

    rows = [
        json.loads(l)
        for l in Path(args.inp).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    ask_rows = [r for r in rows if r["purpose"] == "qa_retrieval"]
    print(f"待预标 {len(ask_rows)} 条 ask 类")

    from collections import Counter

    conf_dist = Counter()
    for r in ask_rows:
        intent = r.get("intent") or r.get("raw_text") or ""
        scored = []
        for c in r.get("top_k_candidates") or []:
            sc, why = score_candidate(intent, c)
            scored.append((sc, c, why))
        scored.sort(key=lambda x: -x[0])

        if not scored:
            r["auto_gold"] = None
            r["confidence"] = "none"
            r["needs_human_annotation"] = True
            conf_dist["none"] += 1
            continue

        best_sc, best, why = scored[0]
        r["auto_gold"] = best["chunk_id"]
        r["auto_gold_score"] = best_sc
        r["auto_gold_reason"] = why
        r["candidate_scores"] = [{"chunk_id": s[1]["chunk_id"], "score": s[0]} for s in scored]
        if best_sc >= args.high_threshold:
            r["confidence"] = "high"
            r["needs_human_annotation"] = False
        elif best_sc >= 3:
            r["confidence"] = "medium"
            r["needs_human_annotation"] = True
        else:
            r["confidence"] = "low"
            r["needs_human_annotation"] = True
        conf_dist[r["confidence"]] += 1

    print(f"\n=== 置信度分布 ===")
    for k, v in conf_dist.most_common():
        print(f"  {k:8s} {v}")

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_high = conf_dist.get("high", 0)
    print(f"\n=== 写出 {out} ===")
    print(f"  high（可直接采用）: {n_high}")
    print(f"  其余 {len(ask_rows) - n_high} 条需人工从候选里选")
    print("\n**注意：这不是「自动标 gold」——高分候选也要人工确认**")
    print(f"   因为 top-1 来自同一检索系统，直接采用会循环论证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
