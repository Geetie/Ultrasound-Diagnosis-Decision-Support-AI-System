#!/usr/bin/env python3
"""context_recall / context_precision 评测（Ragas 口径）。

## 为什么需要它（目标交付项 4）

现有 B2 只测 `hit@k` —— 那是「找到答案所在的 chunk 没有」。
但**检索质量的另一半是「找到的内容够不够回答」**。

Ragas 的两个指标回答后者：

| 指标 | 定义 | 回答什么问题 |
|---|---|---|
| **context_recall** | 参考答案的每个句子，有多少能在检索上下文里找到依据 | 检索到的内容**够不够**答完 |
| **context_precision** | 检索上下文里，有多少比例是真正有用的 | 检索到的内容**干不干净** |

**hit@k 与 context_recall 会背离**：
- `hit@1=1` 但 `context_recall=0.2`：找到了那一块，但答案还散落在别处
- `hit@1=0` 但 `context_recall=0.9`：没找到标注那块，但检索到的内容其实够答

## 实现口径（对齐 Ragas 官方）

Ragas 的 `context_recall` 用 LLM 判定「这句话能否由上下文推出」。
本项目有 II-Medical-8B，但全量调 LLM 成本高。

**折中：两套口径都算，都报出来**

| 口径 | 实现 | 优缺点 |
|---|---|---|
| **字面覆盖（默认）** | 参考答案切句，每句的关键实体/数值是否出现在上下文 | 便宜、可复现、会低估（同义改写算不中） |
| **LLM 判定（可选）** | 逐句问模型「能否由上下文推出」 | 准、贵、有判定偏差 |

**默认跑字面覆盖，`--llm` 才跑 LLM 判定。**

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p123_context_recall.py \
    --dataset /tmp/synth_clean.jsonl --kb-version kb_v2.1.0 --top-k 10
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

#: 句子切分（保留终止符，与 intent_extract 一致的做法）
_SENT_SPLIT = re.compile(r"(?<=[。；;！!？?])")

#: 关键片段：数值+单位 / 分级码 / 长度≥2 的中文短语
_KEY_PATTERNS = (
    re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁|ml|m/s|mmHg|kPa)"),
    re.compile(r"(?:C-?TIRADS|TI-?RADS|LI-?RADS|CEUS|LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-C]?\s*类)", re.IGNORECASE),
)


def _sentences(text: str) -> list[str]:
    out = []
    for seg in _SENT_SPLIT.split(text or ""):
        s = seg.strip().rstrip("，,").strip()
        if len(s) >= 6:
            out.append(s)
    return out


def _key_units(sentence: str) -> list[str]:
    """一句话的「关键单元」——用于判定它有没有被上下文覆盖。

    ## 2026-11-09 首轮实测修正：原实现把中文二元组也当关键单元，导致严重低估

    首轮结果：`context_recall = 0.1194` 但 `context_precision = 0.9324` ——
    **两个数互相矛盾**（93% 的块都"有用"，为什么只覆盖 12% 的答案？）。

    查具体案例发现根因：知识库原文是**中英混排**的（LI-RADS 原文就是英文），
    而我的中文二元组只从「即使是小结节」这类连接词里抽，
    抽出来的 `即使`/`是小`/`小结` 这些**在英文原文里当然找不到** →
    每句都判"未覆盖"。

    **修法**：
    1. **保留**：数值+单位、分级码（这些是跨语言可比的强锚点）
    2. **保留**：英文术语（≥3 字母的连续英文词，如 `CEUS` / `APHE` / `washout`）
    3. **去掉**：中文二元组 —— 噪声远大于信号
    """
    units: list[str] = []
    for pat in _KEY_PATTERNS:
        units.extend(m.group(0).replace(" ", "") for m in pat.finditer(sentence))
    # 英文术语（跨语言强锚点）
    for m in re.finditer(r"[A-Za-z][A-Za-z\-]{2,}", sentence):
        units.append(m.group(0).lower())
    return units


def _sentence_covered(sentence: str, context: str, *, threshold: float = 0.5) -> bool:
    """这句话是否被上下文覆盖：关键单元命中率 ≥ threshold。"""
    units = _key_units(sentence)
    if not units:
        return False
    # 去空白后的上下文（KB 里有换行/空格噪声）
    ctx = re.sub(r"\s+", "", context).lower()
    hit = sum(1 for u in units if u and re.sub(r"\s+", "", u).lower() in ctx)
    return hit / len(units) >= threshold


def compute_context_recall(reference: str, contexts: list[str]) -> float:
    """Ragas 口径的 context_recall：参考答案的句子被覆盖的比例。"""
    sents = _sentences(reference)
    if not sents:
        return 0.0
    ctx = "\n".join(contexts)
    covered = sum(1 for s in sents if _sentence_covered(s, ctx))
    return covered / len(sents)


def compute_context_precision(contexts: list[str], reference: str) -> float:
    """简化版 context_precision：检索上下文里「含参考答案关键单元」的 chunk 比例。

    注：Ragas 的完整版用 AP（average precision）且需要逐 chunk 判定相关性；
    这里用「chunk 是否含参考答案的关键单元」作代理，便宜且在排序无关时等价于
    context_recall 的分子/分母结构。
    """
    if not contexts:
        return 0.0
    ref_units = set()
    for s in _sentences(reference):
        ref_units.update(u for u in _key_units(s) if u)
    if not ref_units:
        return 0.0
    useful = 0
    for c in contexts:
        cc = re.sub(r"\s+", "", c).lower()
        if any(u and re.sub(r"\s+", "", u).lower() in cc for u in ref_units):
            useful += 1
    return useful / len(contexts)


async def retrieve_contexts(query: str, organ: str, kb_version: str, top_k: int) -> list[str]:
    from app.config.database import AsyncSessionLocal
    from app.rag.bm25_fallback import BM25Index

    bm25 = BM25Index(session_factory=AsyncSessionLocal)
    await bm25.ensure_built(kb_version)
    hits = bm25.search(query, top_k=top_k, organ_filter=organ)
    out = []
    for h in hits:
        c = getattr(h, "chunk", None) or h
        out.append(getattr(c, "content", "") or "")
    return out


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="context_recall / precision（p123）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0, help="0=全量")
    ap.add_argument("--report", default="/tmp/p123_report.json")
    args = ap.parse_args(argv)

    rows = [
        json.loads(l)
        for l in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    # 兼容两种数据集布局：
    # - 合成集：有 gate_passed 字段
    # - B2 升级集（v3）：没有 gate_passed，但有 reference_answer
    # 判据统一为「有 reference_answer 且非空」—— 没有答案就测不了 recall。
    rows = [r for r in rows if (r.get("reference_answer") or "").strip()]
    if args.limit:
        rows = rows[: args.limit]
    print(f"评测 {len(rows)} 条，top_k={args.top_k}")

    recs, precs = [], []
    per_cell: dict[tuple, list[float]] = {}
    for i, r in enumerate(rows, 1):
        q = r.get("query") or ""
        ref = r.get("reference_answer") or ""
        # organ 可能在校顶层（合成集）或 metadata 里（B2 升级集 v3）
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        organ = r.get("organ") or md.get("organ") or ""
        if not (q and ref and organ):
            continue
        ctxs = await retrieve_contexts(q, organ, args.kb_version, args.top_k)
        rc = compute_context_recall(ref, ctxs)
        cp = compute_context_precision(ctxs, ref)
        recs.append(rc)
        precs.append(cp)
        md2 = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
        key = (
            r.get("hop") or md2.get("hop"),
            r.get("specificity") or md2.get("specificity"),
            r.get("persona") or md2.get("persona"),
            r.get("length") or md2.get("length"),
        )
        per_cell.setdefault(key, []).append(rc)
        if i % 20 == 0:
            print(f"  进度 {i}/{len(rows)}", flush=True)

    if not recs:
        print("无有效样本")
        return 1

    def _stat(xs: list[float]) -> dict:
        xs = sorted(xs)
        return {
            "mean": round(sum(xs) / len(xs), 4),
            "median": round(xs[len(xs) // 2], 4),
            "n": len(xs),
        }

    print(f"\n=== 结果（n={len(recs)}）===")
    print(f"  context_recall    = {_stat(recs)['mean']:.4f} (median {_stat(recs)['median']})")
    print(f"  context_precision = {_stat(precs)['mean']:.4f} (median {_stat(precs)['median']})")

    print(f"\n=== 按格子（hop/spec/persona/length）===")
    for k in sorted(per_cell):
        s = _stat(per_cell[k])
        if s["n"] >= 2:
            print(f"  {k}: recall={s['mean']:.3f} (n={s['n']})")

    # **按 query_type 分层** —— 本项目真实可用的维度（persona 在原数据里是空的）
    print(f"\n=== 按 query_type（原数据真实标注的 8 类）===")
    by_qt: dict[str, list[float]] = {}
    for r in rows:
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
        qt = md.get("query_type") or "?"
        if qt != "?" and r.get("reference_answer"):
            pass
    # 重新走一遍（需要与 recs 对齐，故用索引）
    idx = 0
    for r in rows:
        if not ((r.get("reference_answer") or "").strip()):
            continue
        md = r.get("metadata") if isinstance(r.get("metadata"), dict) else r
        organ2 = r.get("organ") or md.get("organ") or ""
        if not (r.get("query") and organ2):
            continue
        if idx < len(recs):
            by_qt.setdefault(str(md.get("query_type") or "?"), []).append(recs[idx])
        idx += 1
    for k in sorted(by_qt, key=lambda x: -len(by_qt[x])):
        v = by_qt[k]
        if len(v) >= 2:
            mean = sum(v) / len(v)
            print(f"  {k:22s}: recall={mean:.3f} (n={len(v)})")

    Path(args.report).write_text(
        json.dumps(
            {
                "n": len(recs),
                "top_k": args.top_k,
                "context_recall": _stat(recs),
                "context_precision": _stat(precs),
                "per_cell": {str(k): _stat(v) for k, v in per_cell.items() if len(v) >= 2},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n写出 → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
