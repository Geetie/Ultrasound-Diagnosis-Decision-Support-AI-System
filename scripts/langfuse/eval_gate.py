"""Langfuse 评测集**指标门控**（19 号计划：≥700 例 + 指标门控；A.60 落地为可 CI 的门禁）。

判据（全部取自评测集内容，不引入人工评分）
------------------------------------------
1. **结论非空率 ≥ 95%**：trace 的 output 里必须含结论文本 —— 空输出意味着"医生什么也没拿到"。
2. **技术串泄漏 = 0**：医生可见 output 不得出现英文技术占位串 / 器官代码 / 英文键值对
   （与 `tests/unit/agt/test_grade_extraction_contract.py::TestNoTechnicalStringInDoctorFacingContent`
   同源判据，这里在**真实 trace** 上复验）。
3. **降级率 ≤ 10%**：output 命中降级标记（「无法判断/无法计算/未能解析出/已转人工判读」）的比例。
4. **条目完整性 ≥ 95%**：每条必须同时有 input 与 output（19 号要求"输入+输出+正确口径"入集）。

用法：
    python scripts/langfuse/eval_gate.py --eval-set data/eval/langfuse_eval_set.jsonl
退出码：0 全过；1 有判据不达标；2 缺输入。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 与前端/后端可读化守卫同源的技术串（A.59/A.60）
_TECH_PATTERNS = (
    r"\[(thyroid|liver|cardiac|pipeline)\]",
    r"LVEF\s*=",
    r"severity\s*=",
    r"(grade|lvef|lirads|tirads)_unparseable",
    r"evidence_insufficient",
    r"llm_unavailable",
    r"parse_failed",
    r"specialist_unavailable",
    r"manual review required",
    r"LLM output did not",
)
_TECH_RE = re.compile("|".join(_TECH_PATTERNS), re.IGNORECASE)

_DEGRADED_RE = re.compile(r"无法判断|无法计算|未能解析出|已转人工判读|未测值")


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-set", default="data/eval/langfuse_eval_set.jsonl")
    ap.add_argument("--min-summary-rate", type=float, default=0.95)
    ap.add_argument("--max-degraded-rate", type=float, default=0.10)
    ap.add_argument("--min-complete-rate", type=float, default=0.95)
    args = ap.parse_args()

    path = Path(args.eval_set)
    if not path.is_file():
        print(f"缺评测集文件：{path}（先跑 scripts/langfuse/collect_eval_set.py）")
        return 2
    rows = _load(path)
    if not rows:
        print(f"评测集为空：{path}（0 条无法判定；请确认 trace 是否真的入库）")
        return 2

    total = len(rows)
    with_output = [r for r in rows if (r.get("output") or "").strip()]
    complete = [r for r in rows if (r.get("input") or "").strip() and (r.get("output") or "").strip()]
    leaked = [r for r in rows if _TECH_RE.search(r.get("output") or "")]
    degraded = [r for r in rows if _DEGRADED_RE.search(r.get("output") or "")]

    summary_rate = len(with_output) / total
    complete_rate = len(complete) / total
    degraded_rate = len(degraded) / total

    checks = [
        (
            f"G1 结论非空率 ≥ {args.min_summary_rate:.0%}",
            summary_rate >= args.min_summary_rate,
            f"{len(with_output)}/{total} = {summary_rate:.1%}",
        ),
        ("G2 技术串泄漏 = 0（可读化）", not leaked, f"leaked={len(leaked)}"),
        (
            f"G3 降级率 ≤ {args.max_degraded_rate:.0%}",
            degraded_rate <= args.max_degraded_rate,
            f"{len(degraded)}/{total} = {degraded_rate:.1%}",
        ),
        (
            f"G4 条目完整性 ≥ {args.min_complete_rate:.0%}（输入+输出齐备）",
            complete_rate >= args.min_complete_rate,
            f"{len(complete)}/{total} = {complete_rate:.1%}",
        ),
    ]

    failed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        if not ok:
            failed += 1
    print(f"== eval_gate: {'PASS' if failed == 0 else 'FAIL'}（{len(checks) - failed}/{len(checks)}）")
    if leaked:
        for r in leaked[:3]:
            print(f"  泄漏样例 trace={r.get('trace_id')}: {(_TECH_RE.search(r['output']) or [None])[0]}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
