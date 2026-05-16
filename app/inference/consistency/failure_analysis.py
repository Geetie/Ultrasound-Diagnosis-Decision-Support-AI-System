from typing import Any, Dict, List, Set, Tuple

from app.inference.verification_config import get_verification_config
from .report import ConsistencyPair, ConsistencyReport
from .statistics import text_similarity, _smart_truncate


def generate_failure_analysis(
    report: ConsistencyReport,
    pairs: List[ConsistencyPair],
) -> Dict[str, Any]:
    analysis = {
        "summary": {
            "total_failures": len(set(
                c.get("case_id") for c in report.inconsistent_cases + report.boundary_cases
            )),
            "grading_failures": len(report.inconsistent_cases),
            "boundary_failures": len(report.boundary_cases),
            "critical_boundary_failures": sum(
                1 for c in report.boundary_cases if c.get("is_critical", False)
            ),
            "extraction_failures": 0,
            "hallucination_failures": 0,
            "text_failures": 0,
        },
        "by_organ": {},
        "top_failure_reasons": [],
        "recommendations": [],
        "trace_chain": [],
    }

    inconsistent_ids: Set[str] = {c.get("case_id", "") for c in report.inconsistent_cases}
    boundary_ids: Set[str] = {c.get("case_id", "") for c in report.boundary_cases}

    seen_case_ids: Set[str] = set()
    for case in report.inconsistent_cases + report.boundary_cases:
        case_id = case.get("case_id", "")
        if case_id in seen_case_ids:
            continue
        seen_case_ids.add(case_id)
        organ = case.get("organ", "unknown")
        if organ not in analysis["by_organ"]:
            analysis["by_organ"][organ] = {
                "total": 0,
                "grading_failures": 0,
                "boundary_failures": 0,
                "critical_boundary_failures": 0,
                "overlap_count": 0,
                "clinical_boundary_diff": 0,
            }
        analysis["by_organ"][organ]["total"] += 1
        is_grading = case_id in inconsistent_ids
        is_boundary = case_id in boundary_ids
        if is_grading:
            analysis["by_organ"][organ]["grading_failures"] += 1
        if is_boundary:
            analysis["by_organ"][organ]["boundary_failures"] += 1
            analysis["by_organ"][organ]["clinical_boundary_diff"] += 1
        if is_grading and is_boundary:
            analysis["by_organ"][organ]["overlap_count"] += 1
        if case.get("is_critical"):
            analysis["by_organ"][organ]["critical_boundary_failures"] += 1

    for p in pairs:
        if not p.grading_semantic_consistent or p.is_boundary_crossing or p.expected_vs_actual_boundary_crossing:
            trace = {
                "case_id": p.case_id,
                "organ": p.organ,
                "input": p.prompt,
                "engine_a_output": _smart_truncate(p.engine_a_result.text) if p.engine_a_result else None,
                "engine_b_output": _smart_truncate(p.engine_b_result.text) if p.engine_b_result else None,
                "steps": [],
                "extracted_grading_a": p.engine_a_result.grading if p.engine_a_result else None,
                "extracted_grading_b": p.engine_b_result.grading if p.engine_b_result else None,
                "hallucination_issues_a": p.engine_a_result.hallucination_issues if p.engine_a_result else [],
                "hallucination_issues_b": p.engine_b_result.hallucination_issues if p.engine_b_result else [],
                "text_similarity_score": text_similarity(p.engine_a_result.text, p.engine_b_result.text) if (p.engine_a_result and p.engine_b_result and p.engine_a_result.text and p.engine_b_result.text) else 0.0,
                "boundary_details": {"weight": p.boundary_weight, "is_critical": p.is_critical_boundary} if p.is_boundary_crossing else None,
            }
            if p.engine_a_result and p.engine_a_result.grading is None and p.engine_a_result.text:
                trace["steps"].append({
                    "stage": "grading_extraction",
                    "rule": "extract_grading_failed",
                    "detail": f"engine_a output contains no recognizable grading",
                    "severity": "high",
                })
                analysis["summary"]["extraction_failures"] += 1
            if p.engine_b_result and p.engine_b_result.grading is None and p.engine_b_result.text:
                trace["steps"].append({
                    "stage": "grading_extraction",
                    "rule": "extract_grading_failed",
                    "detail": f"engine_b output contains no recognizable grading",
                    "severity": "high",
                })
                analysis["summary"]["extraction_failures"] += 1
            if not p.grading_semantic_consistent:
                trace["steps"].append({
                    "stage": "grading_check",
                    "rule": "grading_consistency",
                    "detail": f"engine_a={p.engine_a_result.grading if p.engine_a_result else 'N/A'}, engine_b={p.engine_b_result.grading if p.engine_b_result else 'N/A'}",
                    "severity": "high" if p.is_boundary_crossing else "medium",
                })
            if p.is_boundary_crossing:
                grading_a = p.engine_a_result.grading if p.engine_a_result else "N/A"
                grading_b = p.engine_b_result.grading if p.engine_b_result else "N/A"
                severity = "critical" if p.is_critical_boundary else "warning"
                trace["steps"].append({
                    "stage": "boundary_check",
                    "rule": f"boundary_weight={p.boundary_weight}",
                    "detail": f"engine_a={grading_a}, engine_b={grading_b}, severity={severity}",
                    "matched_boundary": f"{grading_a}→{grading_b} (weight={p.boundary_weight}, is_critical={p.is_critical_boundary})",
                    "severity": severity,
                })
            if p.expected_vs_actual_boundary_crossing:
                trace["steps"].append({
                    "stage": "expected_vs_actual_check",
                    "rule": "expected_vs_actual_boundary_crossing",
                    "detail": f"expected={p.expected_grading}, engine_a={p.engine_a_result.grading if p.engine_a_result else 'N/A'}, engine_b={p.engine_b_result.grading if p.engine_b_result else 'N/A'}",
                    "severity": "critical",
                })
            if p.engine_a_result and p.engine_a_result.is_hallucination:
                trace["steps"].append({
                    "stage": "hallucination_check",
                    "rule": "engine_a_hallucination",
                    "detail": "engine_a output flagged as hallucination",
                    "severity": "high",
                })
                analysis["summary"]["hallucination_failures"] += 1
            if p.engine_b_result and p.engine_b_result.is_hallucination:
                trace["steps"].append({
                    "stage": "hallucination_check",
                    "rule": "engine_b_hallucination",
                    "detail": "engine_b output flagged as hallucination",
                    "severity": "high",
                })
                analysis["summary"]["hallucination_failures"] += 1
            if (p.engine_a_result and p.engine_b_result
                    and p.grading_semantic_consistent
                    and text_similarity(p.engine_a_result.text, p.engine_b_result.text) < 0.5):
                trace["steps"].append({
                    "stage": "text_consistency_check",
                    "rule": "text_inconsistency",
                    "detail": f"grading consistent but text similarity low",
                    "severity": "low",
                })
                analysis["summary"]["text_failures"] += 1
            trace["recommendation"] = suggest_for_trace(trace["steps"], p)
            analysis["trace_chain"].append(trace)

    a_fs = report.false_stats.engine_a_stats
    b_fs = report.false_stats.engine_b_stats
    total_fp = (a_fs.false_positives if a_fs else 0) + (b_fs.false_positives if b_fs else 0)
    total_fn = (a_fs.false_negatives if a_fs else 0) + (b_fs.false_negatives if b_fs else 0)
    total_tp = (a_fs.true_positives if a_fs else 0) + (b_fs.true_positives if b_fs else 0)
    fp_fn_diff = total_fp - total_fn
    _ranked: List[Tuple[int, str]] = []
    if report.critical_boundary_diff_count > 0:
        _ranked.append((0, f"存在{report.critical_boundary_diff_count}例关键边界跨越(如4↔5)，需紧急审查模型对边界病例的分级准确性"))
    if report.clinical_boundary_diff_rate > get_verification_config().consistency_thresholds.clinical_boundary_diff_rate:
        _ranked.append((1, "临床边界差异率偏高，建议针对边界病例进行专项优化"))
    for organ_name, organ_data in analysis.get("by_organ", {}).items():
        if organ_data.get("clinical_boundary_diff", 0) > 0:
            organ_bd_rate = report.organ_breakdown.get(organ_name, {}).get("clinical_boundary_diff_rate", 0.0)
            _ranked.append((2, f"{organ_name}器官存在{organ_data['clinical_boundary_diff']}例边界跨越"
                              f"（边界差异率{organ_bd_rate:.2%}，需降至≤1%），"
                              f"建议增加≥100例/类别{organ_name}边界训练数据（参考07-数据标注与模型评估.md）"))
    if total_fn > total_tp:
        _ranked.append((3, "假阴性较高，建议增强幻觉检测的召回率"))
    if fp_fn_diff >= 5 or (total_fn > 0 and fp_fn_diff / max(total_fn, 1) > 1.5):
        _ranked.append((4, "假阳性较高，建议优化禁忌词检测阈值或扩展训练数据"))
    if analysis["summary"]["extraction_failures"] > 0:
        _ranked.append((5, f"存在{analysis['summary']['extraction_failures']}次分级提取失败，建议扩展分级正则模式覆盖范围"))
    thresholds = get_verification_config().consistency_thresholds
    if report.latency_diff_pct > thresholds.latency_diff_pct:
        _ranked.append((6, f"延迟差异{report.latency_diff_pct:.1f}%超过阈值{thresholds.latency_diff_pct}%，"
                         f"建议检查慢引擎的推理配置或模型量化参数"))
    for organ_name, organ_data in report.organ_breakdown.items():
        organ_rate = organ_data.get("grading_consistency_rate", 1.0)
        if organ_rate < thresholds.grading_consistency_rate:
            gap = thresholds.grading_consistency_rate - organ_rate
            needed_cases = int(gap * organ_data.get("total", 100) / max(organ_rate, 0.01))
            _ranked.append((7, f"{organ_name}器官分级一致率{organ_rate:.2%}低于阈值{thresholds.grading_consistency_rate:.0%}"
                              f"（差距{gap:.2%}，约需补充{needed_cases}例训练数据），"
                              f"建议增加{organ_name}专项训练数据"))
    _ranked.sort(key=lambda x: x[0])
    analysis["recommendations"] = [msg for _, msg in _ranked]

    if report.quantization_increment_exceeded:
        analysis["recommendations"].insert(0, {
            "priority": 1,
            "category": "quantization_hallucination",
            "suggestion": f"量化幻觉增量{report.quantization_hallucination_increment:.2%}超过阈值{report._config.benchmark_thresholds.hallucination_increment:.2%}，建议调整量化参数或更换量化方案",
            "metric": "quantization_hallucination_increment",
            "current": report.quantization_hallucination_increment,
            "threshold": report._config.benchmark_thresholds.hallucination_increment if report._config else 0.005,
        })

    return analysis


def suggest_for_trace(steps: List[Dict[str, Any]], pair: ConsistencyPair) -> str:
    rules = [s["rule"] for s in steps]
    grading_a = pair.engine_a_result.grading if pair.engine_a_result else None
    grading_b = pair.engine_b_result.grading if pair.engine_b_result else None
    if "extract_grading_failed" in rules:
        failed_text = ""
        for s in steps:
            if s["rule"] == "extract_grading_failed":
                if "engine_a" in s.get("detail", ""):
                    preview = (pair.engine_a_result.text[:50] if pair.engine_a_result and pair.engine_a_result.text else "")
                    failed_text = f"分级提取失败({pair.organ}): 输出前50字符={preview}"
                    break
                elif "engine_b" in s.get("detail", ""):
                    preview = (pair.engine_b_result.text[:50] if pair.engine_b_result and pair.engine_b_result.text else "")
                    failed_text = f"分级提取失败({pair.organ}): 输出前50字符={preview}"
                    break
        return failed_text or f"分级提取失败({pair.organ})，检查相关正则模式是否覆盖该输出格式"
    if "expected_vs_actual_boundary_crossing" in rules:
        return f"预期vs实际边界跨越({pair.expected_grading}→实际)，需紧急审查模型对{pair.organ}的分级准确性"
    if "grading_consistency" in rules and "boundary_check" in rules:
        severity = "critical" if pair.is_critical_boundary else "warning"
        return f"边界跨越({pair.organ}): {grading_a}→{grading_b}, 权重={pair.boundary_weight}, 严重度={severity}"
    if "grading_consistency" in rules:
        return f"双引擎分级不一致({pair.organ}): engine_a={grading_a}, engine_b={grading_b}"
    if any(s.get("severity") == "critical" for s in steps):
        return "关键边界跨越，需紧急审查模型输出"
    if any(s.get("stage") == "hallucination_check" for s in steps) and any(
        "quantiz" in (s.get("rule", "") or "").lower() or "quantiz" in (s.get("detail", "") or "").lower()
        for s in steps
    ):
        return "检测到幻觉且涉及量化，建议检查量化参数是否导致幻觉率上升"
    return "建议复核该用例的模型推理过程"
