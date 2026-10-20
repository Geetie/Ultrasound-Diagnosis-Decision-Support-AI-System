"""A.69 / R17 续：把「知识断言被判不支持」的样本做成**人工可扫**的核对表 + 自动分流。

**要回答的问题**：知识断言的支持率只有 4.4%，到底是
①**检索到的资料没覆盖**（资料问题），还是
②**判官偏严/断言是推理句**（判官问题）？

**不需要人先看也能给出初判**：对每条"判不支持"的知识断言，算它与**本次检索资料**的
字符 2-gram 重合率（``overlap``）：
* 重合率 **低（<0.2）** → 资料里几乎没有对应内容 → 偏 **COVERAGE_GAP（资料不足）**
* 重合率 **高（≥0.5）** → 资料里有相近表述却判不支持 → 偏 **JUDGE_STRICT（判官偏严）**
* 中间 → **AMBIGUOUS（需人看改写/推理是否合理）**

同时输出一份 Markdown 表（每行一条断言 + 资料摘录），**给医生/项目组 10 分钟扫一遍**即可
判断"这条资料到底支不支持这句话"。

用法（服务器，需要 p44 的报告 + 评测集）：
    ~/uai/venv/bin/python scripts/audit/p45_unsupported_review.py \
        --report ~/uai/reports/faithfulness_caliber_a_zh.json \
        --eval-set ~/uai/data/eval/double_engine_eval_set.jsonl \
        --out-md ~/uai/reports/faithfulness_review.md \
        --out-json ~/uai/reports/faithfulness_review.json

退出码：0 出表；2 前置缺失（报告/评测集不存在，或报告里没有逐条明细）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_LOW = 0.2
_HIGH = 0.5


def bigrams(text: str) -> set[str]:
    """字符 2-gram 集合（中文无空格，按字符滑窗；英文按小写字符也算）。"""
    t = "".join(str(text or "").lower().split())
    return {t[i : i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()


def overlap_ratio(statement: str, context: str) -> float:
    """断言有多少比例的 2-gram 出现在资料里（0~1）。纯函数（守卫用）。"""
    s = bigrams(statement)
    if not s:
        return 0.0
    c = bigrams(context)
    return round(len(s & c) / len(s), 4)


def bucket_of(ratio: float) -> str:
    """按重合率把"不支持"的断言分成三类。纯函数（守卫用）。"""
    if ratio < _LOW:
        return "COVERAGE_GAP"
    if ratio >= _HIGH:
        return "JUDGE_STRICT"
    return "AMBIGUOUS"


def build_rows(report: dict, items_by_id: dict[str, dict], max_rows: int) -> tuple[list[dict], dict]:
    """从 p44 报告 + 评测集拼出核对行，并统计三类分布。纯逻辑（守卫用）。

    只收「有知识断言、且至少一条判不支持」的样本。
    """
    rows: list[dict] = []
    for s in report.get("per_sample") or []:
        detail = s.get("detail") or []
        know = [d for d in detail if d.get("bucket") == "knowledge"]
        bad = [d for d in know if int(d.get("verdict") or 0) == 0]
        if not bad:
            continue
        item = items_by_id.get(str(s.get("request_id")), {})
        ctx = item.get("context")
        context = "\n\n".join(str(x) for x in ctx) if isinstance(ctx, list) else str(ctx or "")
        rows.append(
            {
                "request_id": s.get("request_id"),
                "organ": s.get("organ"),
                "question": (item.get("clinical_text") or "")[:120],
                "conclusion": (item.get("conclusion") or "")[:220],
                "n_knowledge": len(know),
                "n_knowledge_supported": len(know) - len(bad),
                "unsupported": [
                    {
                        "statement": d.get("statement"),
                        "overlap": overlap_ratio(str(d.get("statement") or ""), context),
                        "class": bucket_of(overlap_ratio(str(d.get("statement") or ""), context)),
                    }
                    for d in bad[:3]
                ],
                "n_unsupported": len(bad),
                "context_excerpt": context[:360],
            }
        )
    rows.sort(key=lambda r: (-r["n_unsupported"], str(r["organ"])))
    rows = rows[:max_rows]

    counts = {"COVERAGE_GAP": 0, "AMBIGUOUS": 0, "JUDGE_STRICT": 0}
    for r in rows:
        for u in r["unsupported"]:
            counts[u["class"]] = counts.get(u["class"], 0) + 1
    total = sum(counts.values())
    summary = {
        "rows": len(rows),
        "unsupported_statements_shown": total,
        "class_counts": counts,
        "class_share": {k: (round(v / total, 3) if total else None) for k, v in counts.items()},
        "reading": (
            "COVERAGE_GAP 占多数 → 资料没覆盖（检索/语料问题）；"
            "JUDGE_STRICT 占多数 → 判官偏严（需换判官或放宽 NLI 口径）；"
            "两类都高 → 需人工看 AMBIGUOUS 那批。"
        ),
    }
    return rows, summary


def render_md(rows: list[dict], summary: dict, meta: dict) -> str:
    lines = [
        "# 忠实度：被判「不支持」的知识断言核对表",
        "",
        f"- 来源报告：`{meta['report']}`（断言语言 `{meta.get('statements_lang')}`）",
        f"- 原口径 faithfulness：`{meta.get('faithfulness_original_caliber')}`；"
        f"A 口径：`{meta.get('faithfulness_caliber_a')}`",
        f"- 本表样本数：**{summary['rows']}**；展示的不支持断言："
        f"**{summary['unsupported_statements_shown']}** 条",
        "",
        "## 自动初判（按「断言与资料的 2-gram 重合率」）",
        "",
        "| 类别 | 含义 | 条数 | 占比 |",
        "|---|---|---|---|",
        f"| COVERAGE_GAP | 资料里几乎没有对应内容（<{_LOW}） | {summary['class_counts']['COVERAGE_GAP']} | "
        f"{summary['class_share']['COVERAGE_GAP']} |",
        f"| AMBIGUOUS | 有一定重合（{_LOW}~{_HIGH}），需人看 | {summary['class_counts']['AMBIGUOUS']} | "
        f"{summary['class_share']['AMBIGUOUS']} |",
        f"| JUDGE_STRICT | 资料里有相近表述却判不支持（≥{_HIGH}） | {summary['class_counts']['JUDGE_STRICT']} | "
        f"{summary['class_share']['JUDGE_STRICT']} |",
        "",
        f"> {summary['reading']}",
        "",
        "## 逐条核对（每行只需回答一句：**这段资料支不支持这句话？**）",
        "",
        "| # | 器官 | 判官判「不支持」的知识断言 | 与资料重合 | 自动初判 |",
        "|---|---|---|---|---|",
    ]
    idx = 0
    for r in rows:
        for u in r["unsupported"]:
            idx += 1
            stmt = str(u["statement"]).replace("|", "／")
            lines.append(
                f"| {idx} | {r['organ']} | {stmt} | {u['overlap']} | {u['class']} |"
            )
    lines += ["", "## 样本明细（含问题与资料摘录）", ""]
    for i, r in enumerate(rows, 1):
        lines += [
            f"### {i}. [{r['organ']}] 知识断言 {r['n_knowledge']} 条，其中"
            f" {r['n_unsupported']} 条判不支持",
            "",
            f"- 问题：{r['question'].replace(chr(10), ' ') or '（无临床文本）'}",
            f"- 结论摘录：{r['conclusion'].replace(chr(10), ' ')}",
            f"- 检索资料摘录：{r['context_excerpt'].replace(chr(10), ' ')}",
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/faithfulness_caliber_a_zh.json")
    )
    ap.add_argument(
        "--eval-set", default=str(pathlib.Path.home() / "uai/data/eval/double_engine_eval_set.jsonl")
    )
    ap.add_argument(
        "--out-md", default=str(pathlib.Path.home() / "uai/reports/faithfulness_review.md")
    )
    ap.add_argument(
        "--out-json", default=str(pathlib.Path.home() / "uai/reports/faithfulness_review.json")
    )
    ap.add_argument("--max-rows", type=int, default=20)
    args = ap.parse_args()

    rp = pathlib.Path(args.report).expanduser()
    ep = pathlib.Path(args.eval_set).expanduser()
    if not rp.is_file() or not ep.is_file():
        print(f"[FATAL] 缺文件：report={rp.is_file()} eval_set={ep.is_file()}")
        return 2
    report = json.loads(rp.read_text(encoding="utf-8"))
    if not any(s.get("detail") for s in report.get("per_sample") or []):
        print("[FATAL] 报告里没有逐条明细（p44 需用 --samples-in-report >= 样本数 重跑）")
        return 2

    items = {}
    for line in ep.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        items[str(row.get("request_id"))] = row

    rows, summary = build_rows(report, items, args.max_rows)
    meta = {
        "report": str(rp),
        "statements_lang": report.get("statements_lang"),
        "faithfulness_original_caliber": report.get("faithfulness_original_caliber"),
        "faithfulness_caliber_a": report.get("faithfulness_caliber_a"),
    }
    md = render_md(rows, summary, meta)
    out_md = pathlib.Path(args.out_md).expanduser()
    out_json = pathlib.Path(args.out_json).expanduser()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    out_json.write_text(
        json.dumps({"meta": meta, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"样本 {summary['rows']} 条，不支持断言 {summary['unsupported_statements_shown']} 条")
    print(f"自动初判：{summary['class_counts']}（占比 {summary['class_share']}）")
    print(f"核对表：{out_md}")
    print(f"结构化：{out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
