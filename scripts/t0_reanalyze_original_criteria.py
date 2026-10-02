"""T0：按 01 号设计文档 §5.7 原口径重算双引擎一致性报告，产出正式判定。

口径（全部来自设计文档原文，不是本脚本发明）：
1. 语义分级一致率 ≥98%（"4"与"4a"同属 4 类视为一致；01 号 §5.7）
2. 完整文本一致率 ≥90%（同一模型、同一输入、temperature=0；01 号 §5.7）
3. 量化幻觉增量 ≤0.5%（07 号 §1；即两框架幻觉率差异，07 号一致性验证表同值）
4. 临床决策边界差异率 ≤1%（Gate 0A 阶段一：点估计 ≤3%）
5. 预期 vs 实际边界跨越检测（01 号 §5.7：当两引擎给出相同但错误的分级时仍可触发）

用法：
    python scripts/t0_reanalyze_original_criteria.py \
        --report tests/reports/consistency_780_t0_7b_percase.json \
        --label "7B+结构化+chat（T0 重测）" \
        --output docs/合规/双引擎一致性正式判定-Gate0A口径.json

不带 --report 时对三份历史报告做聚合口径核对。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.inference.verification_config import get_verification_config  # noqa: E402

THRESHOLDS = {
    "semantic_grading_consistency_rate": 0.98,
    "text_consistency_rate": 0.90,
    "quantization_hallucination_increment": 0.005,
    "clinical_boundary_diff_rate": 0.01,  # 正式要求；Gate 0A 阶段一放宽到 0.03
    "clinical_boundary_diff_rate_gate0a_stage1": 0.03,
}


def _main_level(grade: str | None, organ: str) -> int | None:
    """取分级的主级别数字（4a→4，mild→1 …），供边界跨越判定。"""
    if grade is None:
        return None
    from app.inference.grading_definitions import _GRADE_HIERARCHY

    table = _GRADE_HIERARCHY.get(organ, {})
    entry = table.get(grade.strip().lower())
    if entry is None:
        return None
    return entry[0]


def expected_vs_actual_crossings(
    case_rows: list[dict],
) -> dict[str, object]:
    """预期 vs 实际边界跨越检测（01 号 §5.7）。

    对每个带 expected_grading 的用例、每台引擎：预期分级与实际分级是否跨越
    组织级临床决策边界（verification_config.organ_boundaries）。
    特别统计"两引擎一致但均跨越"（原设计：相同但错误仍须触发）。
    """
    config = get_verification_config()
    boundaries: dict[str, list[tuple[int, int, float, bool]]] = {}
    for organ, cfgs in config.boundary_config.organ_boundaries.items():
        rows = []
        for c in cfgs:
            from_main = _main_level(c.from_grade, organ)
            to_main = _main_level(c.to_grade, organ)
            if from_main is not None and to_main is not None:
                rows.append((from_main, to_main, c.weight, c.is_critical))
        boundaries[organ] = rows

    per_engine = {"a": {"total": 0, "crossed": 0, "critical": 0}, "b": {"total": 0, "crossed": 0, "critical": 0}}
    both_same_wrong_total = 0
    both_same_wrong_crossed = 0
    examples: list[dict] = []

    def _crossed(
        rules: list[tuple[int, int, float, bool]],
        expected_main: int | None,
        engine_main: int | None,
    ) -> tuple[bool, bool]:
        if engine_main is None or expected_main is None:
            return False, False
        crossed = critical = False
        for f, t, _w, crit in rules:
            if {expected_main, engine_main} == {f, t} and expected_main != engine_main:
                crossed, critical = True, crit
        return crossed, critical

    for row in case_rows:
        expected = row.get("expected_grading")
        if not expected:
            continue
        organ = row.get("organ", "")
        rules = boundaries.get(organ, [])
        ga = _main_level(row.get("grading_a"), organ)
        gb = _main_level(row.get("grading_b"), organ)
        em = _main_level(expected, organ)

        ca, crit_a = _crossed(rules, em, ga)
        cb, crit_b = _crossed(rules, em, gb)
        per_engine["a"]["total"] += 1
        per_engine["b"]["total"] += 1
        if ca:
            per_engine["a"]["crossed"] += 1
            per_engine["a"]["critical"] += int(crit_a)
        if cb:
            per_engine["b"]["crossed"] += 1
            per_engine["b"]["critical"] += int(crit_b)

        # 两引擎一致（互相一致）但均跨越预期边界
        if (
            row.get("grading_a")
            and row.get("grading_a") == row.get("grading_b")
            and ca
        ):
            both_same_wrong_total += 1
            both_same_wrong_crossed += 1
            if len(examples) < 10:
                examples.append(
                    {
                        "case_id": row["case_id"],
                        "organ": organ,
                        "expected": expected,
                        "both_grading": row["grading_a"],
                    }
                )

    def _rate(d: dict) -> float | None:
        return round(d["crossed"] / d["total"], 4) if d["total"] else None

    return {
        "per_engine": {
            "engine_a": {**per_engine["a"], "crossing_rate": _rate(per_engine["a"])},
            "engine_b": {**per_engine["b"], "crossing_rate": _rate(per_engine["b"])},
        },
        "both_engines_same_but_crossed_expected": {
            "total": both_same_wrong_total,
            "crossed": both_same_wrong_crossed,
            "examples": examples,
        },
    }


def judge_report(report: dict, label: str) -> dict:
    """对单份报告按原口径逐项判定。"""
    def _f(key: str) -> float:
        v = report.get(key)
        return float(v) if v is not None else 0.0

    semantic = _f("semantic_grading_consistency_rate")
    text = _f("text_consistency_rate")
    quant = _f("quantization_hallucination_increment")
    boundary = _f("clinical_boundary_diff_rate")

    verdict = {
        "label": label,
        "total_cases": report.get("total_cases"),
        "criteria": [
            {
                "criterion": "语义分级一致率（'4'='4a' 同类算一致）",
                "threshold": "≥ 98%",
                "actual": f"{semantic:.2%}",
                "passed": semantic >= THRESHOLDS["semantic_grading_consistency_rate"],
            },
            {
                "criterion": "完整文本一致率（同模型同输入 temp=0）",
                "threshold": "≥ 90%",
                "actual": f"{text:.2%}",
                "passed": text >= THRESHOLDS["text_consistency_rate"],
            },
            {
                "criterion": "幻觉率差异（两框架幻觉率之差，07 号一致性表）",
                "threshold": "≤ 0.5%",
                "actual": f"{_f('hallucination_rate_diff'):.2%}",
                "passed": _f("hallucination_rate_diff")
                <= THRESHOLDS["quantization_hallucination_increment"],
            },
            {
                "criterion": "量化幻觉增量（同栈 Q4 vs fp16 基线对照）",
                "threshold": "≤ 0.5%",
                "actual": "未计算" if quant == 0.0 else f"{quant:.2%}",
                "passed": None,  # CLI 未接 QuantizationHallucinationVerifier，列入处置
            },
            {
                "criterion": "临床决策边界差异率（Gate 0B 正式）",
                "threshold": "≤ 1%",
                "actual": f"{boundary:.2%}",
                "passed": boundary <= THRESHOLDS["clinical_boundary_diff_rate"],
            },
            {
                "criterion": "临床决策边界差异率（Gate 0A 阶段一点估计）",
                "threshold": "≤ 3%",
                "actual": f"{boundary:.2%}",
                "passed": boundary <= THRESHOLDS["clinical_boundary_diff_rate_gate0a_stage1"],
            },
        ],
        "gate_status": report.get("gate_status"),
    }

    # 01 号条件性门控规则
    if semantic >= 0.95 and boundary <= 0.01:
        verdict["disposition"] = "通过"
    elif semantic >= 0.90 or boundary <= 0.02:
        verdict["disposition"] = (
            "条件性通过：须在 NMPA 注册材料中特别声明偏差率；语义一致率 ≥98% 单项仍须整改"
        )
    else:
        verdict["disposition"] = (
            "不通过：分级一致率 <90% 或边界差异率 >2% → 按原设计须统一使用单一推理框架，"
            "或完成整改后复测"
        )
    if _f("hallucination_rate_diff") > 0.005:
        verdict["disposition_hallucination_diff"] = (
            "两框架幻觉率差异超标 → 按原设计处置：切换更高精度量化（Q5_K_M → Q8_0）重测；"
            "量化幻觉增量（同栈对照）当前 CLI 未计算，需补 QuantizationHallucinationVerifier 基线对照"
        )
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="T0 原口径重算与正式判定")
    parser.add_argument("--report", default=None, help="含 case_rows 的报告 JSON（7B T0 重测）")
    parser.add_argument(
        "--historical", nargs="*", default=[],
        help="历史报告 JSON 列表（仅聚合口径核对，无逐例）",
    )
    parser.add_argument("--labels", nargs="*", default=[])
    parser.add_argument("--output", default=None, help="判定 JSON 输出路径")
    args = parser.parse_args()

    result: dict = {"criteria_source": "01-系统架构设计.md §5.7 + 07-数据标注与模型评估.md §1"}

    historical = (
        list(zip(args.labels, args.historical, strict=True))
        if args.labels
        else [(Path(p).stem, p) for p in args.historical]
    )
    result["historical_verdicts"] = []
    for label, path in historical:
        with open(path, encoding="utf-8") as f:
            result["historical_verdicts"].append(judge_report(json.load(f), label))

    if args.report:
        with open(args.report, encoding="utf-8") as f:
            report = json.load(f)
        result["current_verdict"] = judge_report(report, "7B+结构化+chat（T0）")
        case_rows = report.get("case_rows") or []
        result["expected_vs_actual"] = expected_vs_actual_crossings(case_rows)

    out = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"written: {args.output}")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
