import json
from typing import Any, Dict

from .report import ConsistencyReport


def report_to_dict(report: ConsistencyReport) -> Dict[str, Any]:
    return {
        "engine_a": report.engine_a_name,
        "engine_b": report.engine_b_name,
        "total_cases": report.total_cases,
        "grading_consistency_rate": f"{report.grading_consistency_rate:.4f}",
        "grading_consistent_count": report.grading_consistent_count,
        "clinical_boundary_diff_rate": f"{report.clinical_boundary_diff_rate:.4f}",
        "boundary_crossing_rate": f"{report.boundary_crossing_rate:.4f}",
        "clinical_boundary_diff_count": report.clinical_boundary_diff_count,
        "clinical_boundary_diff_rate_ci_lower": f"{report.clinical_boundary_diff_rate_ci_lower:.4f}",
        "clinical_boundary_diff_rate_ci_upper": f"{report.clinical_boundary_diff_rate_ci_upper:.4f}",
        "total_boundary_weight": report.total_boundary_weight,
        "critical_boundary_diff_count": report.critical_boundary_diff_count,
        "weighted_boundary_diff_rate": f"{report.weighted_boundary_diff_rate:.4f}",
        "hallucination_rate_diff": f"{report.hallucination_rate_diff:.4f}",
        "text_consistency_rate": f"{report.text_consistency_rate:.4f}",
        "text_consistency_rate_strict": f"{report.text_consistency_rate_strict:.4f}",
        "text_exact_match_rate": f"{report.text_exact_match_rate:.4f}",
        "clinical_consistency_rate": f"{report.clinical_consistency_rate:.4f}",
        "semantic_grading_consistency_rate": f"{report.semantic_grading_consistency_rate:.4f}",
        "clinical_grading_consistency_rate": f"{report.clinical_grading_consistency_rate:.4f}",
        "text_similarity_avg": f"{report.text_similarity_avg:.4f}",
        "quantization_hallucination_increment": round(report.quantization_hallucination_increment, 6),
        "quantization_baseline_rate": round(report.quantization_baseline_rate, 6),
        "quantization_test_rate": round(report.quantization_test_rate, 6),
        "quantization_increment_exceeded": report.quantization_increment_exceeded,
        "latency_a_p50_ms": round(report.latency_a_p50_ms, 2),
        "latency_a_p99_ms": round(report.latency_a_p99_ms, 2),
        "latency_b_p50_ms": round(report.latency_b_p50_ms, 2),
        "latency_b_p99_ms": round(report.latency_b_p99_ms, 2),
        "latency_diff_pct": round(report.latency_diff_pct, 2),
        "latency_diff_p50_pct": round(report.latency_diff_p50_pct, 2),
        "latency_p99_reliable": report.latency_p99_reliable,
        "latency_sample_count_a": report.latency_sample_count_a,
        "latency_sample_count_b": report.latency_sample_count_b,
        "organ_breakdown": report.organ_breakdown,
        "boundary_cases_count": len(report.boundary_cases),
        "inconsistent_cases_count": len(report.inconsistent_cases),
        "false_stats": {
            "false_positives": report.false_stats.false_positives,
            "false_negatives": report.false_stats.false_negatives,
            "true_positives": report.false_stats.true_positives,
            "true_negatives": report.false_stats.true_negatives,
            "cases_with_ground_truth": report.false_stats.cases_with_ground_truth,
            "precision": report.false_stats.precision,
            "recall": report.false_stats.recall,
            "f1_score": report.false_stats.f1_score,
            "engine_a_stats": {
                "false_positives": report.false_stats.engine_a_stats.false_positives,
                "false_negatives": report.false_stats.engine_a_stats.false_negatives,
                "true_positives": report.false_stats.engine_a_stats.true_positives,
                "true_negatives": report.false_stats.engine_a_stats.true_negatives,
                "precision": report.false_stats.engine_a_stats.precision,
                "recall": report.false_stats.engine_a_stats.recall,
                "f1_score": report.false_stats.engine_a_stats.f1_score,
            } if report.false_stats.engine_a_stats else None,
            "engine_b_stats": {
                "false_positives": report.false_stats.engine_b_stats.false_positives,
                "false_negatives": report.false_stats.engine_b_stats.false_negatives,
                "true_positives": report.false_stats.engine_b_stats.true_positives,
                "true_negatives": report.false_stats.engine_b_stats.true_negatives,
                "precision": report.false_stats.engine_b_stats.precision,
                "recall": report.false_stats.engine_b_stats.recall,
                "f1_score": report.false_stats.engine_b_stats.f1_score,
            } if report.false_stats.engine_b_stats else None,
            "strategy_breakdown": {
                s: {
                    "strategy": v.strategy,
                    "false_positives": v.false_positives,
                    "false_negatives": v.false_negatives,
                    "true_positives": v.true_positives,
                    "true_negatives": v.true_negatives,
                }
                for s, v in report.false_stats.strategy_breakdown.items()
            } if report.false_stats.strategy_breakdown else {},
        },
        "failure_analysis": report.failure_analysis,
        "test_data_version": report.test_data_version,
        "passed": report.passed,
        "gate_status": report.gate_status.value,
        "phase_upgraded": report.phase_upgraded,
        "phase_upgrade_from": report.phase_upgrade_from,
        "phase_upgrade_to": report.phase_upgrade_to,
        "elapsed_seconds": round(report.elapsed_seconds, 2),
    }


def report_to_json(report: ConsistencyReport, indent: int = 2) -> str:
    return json.dumps(report_to_dict(report), indent=indent, ensure_ascii=False)
