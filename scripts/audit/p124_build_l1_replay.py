#!/usr/bin/env python3
"""构建 L1 生产回放数据集（目标交付项 1）。

## L1 与 L2 的本质区别

| | L2 受控合成 | **L1 生产回放** |
|---|---|---|
| query 来源 | 知识图遍历 + LLM 生成 | `diagnosis_results.clinical_text` **真实提交** |
| 形态 | 短问句为主 | **长描述（58-77 字）+ 陈述式报告** |
| gold 来源 | 关系对里的节点 | **需从真实文本推导** —— 这是难点 |
| 用途 | 覆盖矩阵 | **测真实场景**（含陈述式） |

## L1 的 gold 怎么定（不编造）

真实文本没有标准答案。**三种情形分别处理**：

### 情形 1：文本本身含明确提问（135 条中文含问句）
→ 用意图抽取得到检索意图 → 检索 top-k → **人工审核**哪块真能回答
→ 该块进 gold。审核不过的标 `answerable: false`（这也是真实信息）

### 情形 2：文本是陈述式报告（如「肝实质未见异常」）
→ 这类医生意图是「让系统生成判读」而不是「问问题」
→ **不适合 hit@k 评测**，但适合测「意图抽取 + 端到端生成」
→ 单独标记 `purpose: "report_generation"`，不进 hit@k 统计

### 情形 3：文本是自造演练/冒烟测试
→ **剔除**（那不是真实数据）

## 为什么 L1 不能自动生成 gold

自动取 top-1 当 gold 是**循环论证**：用检索结果当标准答案，再去测检索。

所以本脚本**只做筛选与预处理**，输出待人工审核的清单：
```
{query, organ, purpose, intent, top_5_candidates, gold: null, needs_gold_annotation: true}
```
**gold 必须人工填。**

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p124_build_l1_replay.py \
    --out /tmp/l1_pending.jsonl
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

_ASK_MARKERS = (
    "？", "?", "要不要", "是否需要", "是否", "怎么", "如何", "为什么",
    "什么", "多少", "下一步", "请", "建议", "推荐", "能否", "该不该",
)

#: 自造/测试数据的标记（剔除）
_FAKE_MARKERS = ("自造演练", "冒烟", "smoke", "test data", "演练数据")


def _lang_of(text: str) -> str:
    """判语言：zh / en / mixed。

    ## 为什么必须做这一步（2026-11-09 实测）

    L1 首轮构建出 554 条，其中 **293 条是纯英文**（如
    `Within the thyroid gland, a nodule is identified containing cystic components.`）。

    这些来自 2026-09-29~10-01 的早期链路测试 —— **不是中国医生会输入的东西**。
    用它们检索中英混排的知识库，top-1 抓到的是零碎英文片段：
      `resources/guidelines/thyroid.pdf). Thyroid US can answer the following questions`

    **这不是重排问题，是语言不匹配的根本障碍。**

    处置：纯英文**不进 L1 评测**（它们是历史测试样本，不反映真实使用场景）。
    中文与中英混排保留 —— 混排的通常是「中文主诉 + 英文缩写」，是真实形态。
    """
    t = text or ""
    zh = len(re.findall(r"[\u4e00-\u9fff]", t))
    en = len(re.findall(r"[A-Za-z]{3,}", t))
    if zh >= 20:
        return "zh"
    if en >= 5 and zh < 5:
        return "en"
    return "mixed"


def _classify(text: str) -> str:
    """把真实文本分成三类：ask / report / fake。"""
    t = text or ""
    low = t.lower()
    if any(m in low or m in t for m in _FAKE_MARKERS):
        return "fake"
    if any(m in t for m in _ASK_MARKERS):
        return "ask"
    return "report"


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建 L1 生产回放待审清单（p124）")
    ap.add_argument("--out", default="/tmp/l1_pending.jsonl")
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--min-len", type=int, default=20)
    args = ap.parse_args(argv)

    from app.config.database import AsyncSessionLocal
    from app.rag.bm25_fallback import BM25Index
    from app.rag.intent_extract import extract_intent
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT organ, clinical_text FROM diagnosis_results "
                    "WHERE clinical_text IS NOT NULL AND length(clinical_text) >= :n"
                ),
                {"n": args.min_len},
            )
        ).fetchall()

    print(f"DB 里长度≥{args.min_len} 的文本: {len(rows)}")

    # 分类统计
    from collections import Counter

    cats = Counter()
    lang_cats: Counter = Counter()
    work: list[dict] = []
    for organ, t in rows:
        t = (t or "").strip()
        kind = _classify(t)
        cats[kind] += 1
        if kind == "fake":
            continue
        lang = _lang_of(t)
        lang_cats[lang] += 1
        if lang == "en":
            continue    # 纯英文：早期测试样本，不进 L1 评测（见 _lang_of 说明）
        work.append({"organ": organ, "raw": t, "kind": kind, "lang": lang, "len": len(t)})

    print(f"\n=== 分类 ===")
    for k, v in cats.most_common():
        print(f"  {k:8s} {v}")
    print(f"\n=== 语言 ===")
    for k, v in lang_cats.most_common():
        print(f"  {k:8s} {v}")
    print(f"  → 纯英文已剔除（早期测试样本，非真实使用场景）")
    print(f"  可用（去 fake + 去英文）: {len(work)}")

    # 只有 ask 类需要 gold；report 类单独标记用途
    ask_items = [w for w in work if w["kind"] == "ask"]
    print(f"\nask 类 {len(ask_items)} 条需要 gold 标注")

    bm25 = BM25Index(session_factory=AsyncSessionLocal)
    await bm25.ensure_built(args.kb_version)

    out_rows: list[dict] = []
    for i, w in enumerate(work, 1):
        raw = w["raw"]
        organ = w["organ"]
        intent = extract_intent(raw) if w["kind"] == "ask" else raw
        # 取 top-k 候选供人工审核（**不自动当 gold**）
        hits = bm25.search(intent, top_k=args.top_k, organ_filter=organ)
        cands = []
        for h in hits:
            c = getattr(h, "chunk", None) or h
            cands.append(
                {
                    "chunk_id": h.chunk_id,
                    "preview": (getattr(c, "content", "") or "").replace("\n", " ")[:150],
                }
            )
        out_rows.append(
            {
                "l1_id": f"l1-{i:04d}",
                "organ": organ,
                "raw_text": raw,
                "raw_len": len(raw),
                "purpose": "qa_retrieval" if w["kind"] == "ask" else "report_generation",
                "lang": w["lang"],
                "intent": intent,
                "top_k_candidates": cands,
                "gold": None,                      # **必须人工填**
                "needs_gold_annotation": w["kind"] == "ask",
                "answerable": None,                # 人工定
                "source": "production",
            }
        )
        if i % 50 == 0:
            print(f"  预处理 {i}/{len(work)}", flush=True)

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_ask = sum(1 for r in out_rows if r["purpose"] == "qa_retrieval")
    n_rep = len(out_rows) - n_ask
    print(f"\n=== 写出 {out} ===")
    print(f"  qa_retrieval（需标 gold）: {n_ask}")
    print(f"  report_generation（测端到端）: {n_rep}")
    print(f"\n**下一步：{n_ask} 条需人工从 top-5 候选里选真能回答的那块**")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
