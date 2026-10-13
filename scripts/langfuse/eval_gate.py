"""Langfuse 评测集**指标门控**（19 号计划：≥700 例 + 指标门控；A.61 落地为可 CI 的门禁）。

两层门控（**分层是刻意的**）
--------------------------
**L1 内容门控（恒跑，纯 CPU、无 LLM）** —— 直接判"这条样本能不能进评测集 / 有没有
把技术串泄漏给医生"：

| 判据 | 阈值 |
|---|---|
| G1 结论非空率 | ≥ 95% |
| G2 技术串泄漏（器官代码 / 英文键值 / 降级串） | **= 0** |
| G3 降级率（无法判断 / 未能解析出 / 已转人工判读…） | ≤ 10% |
| G4 条目完整性（输入 + 输出齐备） | ≥ 95% |

⚠️ **历史样本 vs 近端样本（棘轮口径，A.61 真机踩到）**
评测集是**累积资产**：里面有 A.59「医生可见正文可读化」之前的旧判读，它们的结论
**必然**带 `[thyroid]` 这类技术串（那正是 A.59 要修的东西）。若把这些样本也纳入
门禁，G2 会**永远红**（首跑实测 118/142 泄漏），门禁随即失去意义 —— 与"棘轮只许
下调、存量不作为新账"同一条教训。因此本脚本把结果分成两份：

* `enforced`：`created_at >= --enforce-since`（默认 **2026-10-13**，即可读化上线日）
  的样本 —— **只有它决定退出码**；
* `informational`：全部样本 —— 只**告知**（存量体检），不判红。

近端样本为空时退出码为 0，但报告里显式标 `status: "no_recent_samples"`
（"没样本"不等于"达标"）。

**L2 忠实度门控（faithfulness ≥ 0.95 / 答案相关性 ≥ 0.90，19 号原文）** —— 需要
LLM 判官（RAGAS 口径），由 `scripts/langfuse/judge_eval_set.py` 产出判官报告后传
`--judge-report <json>` 纳入门禁。

📌 **A.61 更正（台账级事实）**：A.61 首版曾写「本仓库没有 RAGAS 判官脚本」—— **这是错的**。
仓库里一直有 `scripts/prompts/ragas_faithfulness.py`（官方 prompt 原文，Apache-2.0）与
`scripts/t1_eval_faithfulness.py`（复用它的端到端实现）；当时的检索把 `scripts/**` 漏掉了。
现已接上：`judge_eval_set.py` **直接复用**这两处的渲染/容错/判分（不自造第二套口径）。

**部分判官报告（如实，不按 0 判红）**：RAGAS 官方 `answer_relevancy` 的 prompt
**本仓库确实未收录**（`scripts/prompts/` 只有 faithfulness 一份），因此判官报告里
`answer_relevance: null` → 本脚本只对**存在的**指标加判据，缺的记进
`judge.metrics_not_measured`（写成 0.0 会把"没测"当成"不及格"，那是另一种静默）。

用法：
    python scripts/langfuse/eval_gate.py [--eval-set <jsonl>] [--report <json>] [--judge-report <json>]
退出码：0 全过；1 有 L1 判据不达标（或提供了 L2 报告且不达标）；2 缺输入。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 与前端/后端可读化守卫同源的技术串（A.59/A.60/A.61）
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

_FAITHFULNESS_MIN = 0.95
_RELEVANCE_MIN = 0.90


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _text(row: dict, *keys: str) -> str:
    """按候选键取文本（兼容两版 schema：A.60 的 input/output 与 A.61 的 clinical_text/conclusion）。"""
    for k in keys:
        val = row.get(k)
        if isinstance(val, str) and val:
            return val
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--eval-set",
        default=str(Path.home() / "uai/data/eval/double_engine_eval_set.jsonl"),
    )
    ap.add_argument("--report", default=str(Path.home() / "uai/reports/langfuse_eval_gate.json"))
    ap.add_argument("--judge-report", default=None, help="L2 LLM 判官报告（缺省则 L2 记 not_run）")
    ap.add_argument(
        "--judge-mode",
        choices=("advisory", "gate"),
        default="advisory",
        help=(
            "advisory（默认）= L2 照常测量并展示，但**不决定退出码**；gate = L2 不达标即判红。"
            "为什么默认 advisory（A.61 实测）：当前样本构成（探针源 + 结论里含系统派生的分级/总分）"
            "会让 RAGAS faithfulness 系统性偏低（实测 raw 0.0798 / reasoning 0.1891），"
            "把未拍板的口径直接当门禁＝红灯噪声；口径拍板后改 `--judge-mode gate` 即可，"
            "**报告里仍显式记 `ok=false` + `advisory: true`**，不会被人误读成达标。"
        ),
    )
    ap.add_argument(
        "--enforce-since",
        default="2026-10-13T08:00:00",
        help=(
            "只对该时刻（UTC，ISO8601 前缀比较）起的样本判红。默认 = 医生可见正文可读化上线时刻"
            "（实测分界：07:31:37Z 是最后一条带技术串的样本，08:15 起全部可读）。"
            "生产复跑请传**本次部署时间戳**，避免把「当天早些时候的旧代码样本」算进新账。"
        ),
    )
    ap.add_argument("--min-summary-rate", type=float, default=0.95)
    ap.add_argument("--max-degraded-rate", type=float, default=0.10)
    ap.add_argument("--min-complete-rate", type=float, default=0.95)
    args = ap.parse_args()

    path = Path(args.eval_set).expanduser()
    if not path.is_file():
        print(f"缺评测集文件：{path}（先跑 scripts/langfuse/collect_eval_set.py）")
        return 2
    rows = _load(path)
    if not rows:
        print(f"评测集为空：{path}（0 条无法判定）")
        return 2

    total = len(rows)
    labeled = sum(1 for r in rows if r.get("corrections"))

    def _checks(subset: list[dict]) -> tuple[list[tuple[str, bool, str]], list[str], dict]:
        """对给定子集算 G1–G4；返回 (checks, 泄漏样例, 明细)。空子集返回空 checks。"""
        n = len(subset)
        if not n:
            return [], [], {}
        conclusions = [_text(r, "conclusion", "output") for r in subset]
        inputs = [_text(r, "clinical_text", "input") for r in subset]
        with_output = [c for c in conclusions if c.strip()]
        leaked = [c for c in conclusions if _TECH_RE.search(c)]
        degraded_idx = [i for i, c in enumerate(conclusions) if _DEGRADED_RE.search(c)]
        # ⚠️ G4 的输入判据（A.61 修正语义）：这是**影像**评测集 —— 输入是影像，
        # `clinical_text` 只是可选上下文（影像-only 判读是合法形态）。原先要求
        # `clinical_text` 非空是 A.60 trace 时代的口径，会误判"影像腿"样本为缺件。
        # 现在 = （有文本 **或** 有影像标识）且 结论非空。空文本的占比**单独报告**
        # （`input_text_present`），不因为这个修正而把"空文本"藏起来。
        complete = [
            1
            for r, i, c in zip(subset, inputs, conclusions, strict=True)
            if ((i.strip() or r.get("dedup_key") or r.get("image_ids")) and c.strip())
        ]
        summary_rate = len(with_output) / n
        complete_rate = len(complete) / n
        degraded_rate = len(degraded_idx) / n
        text_present = sum(1 for i in inputs if i.strip()) / n
        by_organ: dict[str, int] = {}
        for i in degraded_idx:
            organ = str(subset[i].get("organ") or "?")
            by_organ[organ] = by_organ.get(organ, 0) + 1
        detail = {
            "input_text_present_rate": round(text_present, 4),
            "degraded_by_organ": by_organ,
        }
        checks = [
            (
                f"G1 结论非空率 ≥ {args.min_summary_rate:.0%}",
                summary_rate >= args.min_summary_rate,
                f"{len(with_output)}/{n} = {summary_rate:.1%}",
            ),
            ("G2 技术串泄漏 = 0（可读化）", not leaked, f"leaked={len(leaked)}"),
            (
                f"G3 降级率 ≤ {args.max_degraded_rate:.0%}",
                degraded_rate <= args.max_degraded_rate,
                f"{len(degraded_idx)}/{n} = {degraded_rate:.1%} 按器官={by_organ}",
            ),
            (
                f"G4 条目完整性 ≥ {args.min_complete_rate:.0%}（影像或文本 + 结论）",
                complete_rate >= args.min_complete_rate,
                f"{len(complete)}/{n} = {complete_rate:.1%}（clinical_text 非空率 {text_present:.1%}）",
            ),
        ]
        return checks, leaked, detail

    # 整体（含可读化之前的存量样本）—— 只告知，不判红
    all_checks, all_leaked, all_detail = _checks(rows)
    # 近端（>= --enforce-since）—— **决定退出码**
    # ISO8601 字符串前缀比较即可（`2026-10-13T09:12:33 >= 2026-10-13`），无需解析时区
    recent = [r for r in rows if str(r.get("created_at") or "") >= args.enforce_since]
    checks, leaked, detail = _checks(recent)

    # ---- L2：忠实度（需 LLM 判官；缺判官报告则如实记 not_run）----
    # `advisory_checks` = 照常测量与展示、但**不决定退出码**（默认口径，见 --judge-mode 说明）
    advisory_checks: list[tuple[str, bool, str]] = []
    judge: dict = {
        "status": "not_run",
        "reason": "本轮未提供判官报告（`scripts/langfuse/judge_eval_set.py --judge-report`）",
    }
    if args.judge_report:
        jp = Path(args.judge_report).expanduser()
        if not jp.is_file():
            print(f"给了 --judge-report 但文件不存在：{jp}")
            return 2
        data = json.loads(jp.read_text(encoding="utf-8"))
        raw_faith = data.get("faithfulness")
        raw_rel = data.get("answer_relevance")
        not_measured = [
            k
            for k, v in (("faithfulness", raw_faith), ("answer_relevance", raw_rel))
            if v is None
        ]
        judge = {
            "status": "ran",
            "engine": data.get("engine"),
            "judge_url": data.get("judge_url"),
            "model": data.get("model"),
            "answer_mode": data.get("answer_mode"),
            "n": data.get("n"),
            "skipped_no_context": data.get("skipped_no_context"),
            "failure_count": data.get("failed"),
            "wall_s": data.get("wall_s"),
            "faithfulness": raw_faith,
            "answer_relevance": raw_rel,
            "metrics_not_measured": not_measured,
            "thresholds": {"faithfulness": _FAITHFULNESS_MIN, "answer_relevance": _RELEVANCE_MIN},
        }
        target = checks if args.judge_mode == "gate" else advisory_checks
        if raw_faith is not None:
            faith = float(raw_faith)
            target.append(
                (
                    f"L2 faithfulness ≥ {_FAITHFULNESS_MIN}",
                    faith >= _FAITHFULNESS_MIN,
                    f"{faith}（n={data.get('n')}，口径={data.get('answer_mode')}，"
                    f"judge={data.get('model')}）",
                )
            )
        if raw_rel is not None:
            rel = float(raw_rel)
            target.append(
                (f"L2 答案相关性 ≥ {_RELEVANCE_MIN}", rel >= _RELEVANCE_MIN, f"{rel}")
            )
        if not_measured:
            print(f"  （L2 未测指标：{not_measured} —— 官方 prompt 未收录，**不按 0 判红**）")
        if args.judge_mode == "advisory":
            judge["enforcement"] = "advisory"
            judge["advisory_note"] = (
                "口径待拍板（结论里的派生决策是否计入）+ 当前样本为探针源；"
                "`--judge-mode gate` 即纳入退出码。报告里 ok=false 原样保留，不读成达标。"
            )

    print(f"—— 近端样本（>= {args.enforce_since}，{len(recent)} 条；**决定退出码**）——")
    failed = 0
    if not checks:
        print("  （近端样本为空 → `no_recent_samples`：没样本不等于达标）")
    for name, ok, desc in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {desc}")
        if not ok:
            failed += 1
    for c in leaked[:3]:
        m = _TECH_RE.search(c)
        print(f"  泄漏样例：{m.group(0) if m else '?'}")
    if advisory_checks:
        print(f"—— L2 判官（{judge.get('enforcement')}：展示但**不决定退出码**）——")
        for name, ok, desc in advisory_checks:
            print(f"[{'PASS' if ok else '告知'}] {name} — {desc}")
        print(f"  （{judge.get('advisory_note')}）")

    print(f"—— 全部样本（{total} 条；存量体检，仅告知）——")
    for name, ok, desc in all_checks:
        print(f"[{'PASS' if ok else '提醒'}] {name} — {desc}")
    for c in all_leaked[:3]:
        m = _TECH_RE.search(c)
        print(f"  存量泄漏样例（A.59 可读化之前的旧判读）：{m.group(0) if m else '?'}")
    print(f"评测集：{total} 条（医生标注坏例 {labeled}）· L2 判官：{judge['status']}")

    if checks:
        print(f"  （明细）clinical_text 非空率 {detail.get('input_text_present_rate')}"
              f"；降级按器官 {detail.get('degraded_by_organ')}")

    status = "ran" if checks else "no_recent_samples"
    report = {
        "eval_set": str(path),
        "total": total,
        "doctor_labeled": labeled,
        "enforce_since": args.enforce_since,
        "enforced": {
            "n": len(recent),
            "status": status,
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            "detail": detail,
        },
        "informational": {
            "n": total,
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in all_checks],
            "detail": all_detail,
        },
        "advisory": [
            {"name": n, "ok": ok, "detail": d} for n, ok, d in advisory_checks
        ],
        "judge": judge,
        "passed": failed == 0,
        "layers": {"L1_content": status, "L2_faithfulness": judge["status"]},
    }
    rp = Path(args.report).expanduser()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== eval_gate: {'PASS' if failed == 0 else 'FAIL'}"
          f"（近端 {len(checks) - failed}/{len(checks)}，样本 {len(recent)}）  报告：{rp}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
