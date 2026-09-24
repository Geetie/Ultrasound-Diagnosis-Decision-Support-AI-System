
from app.inference.utils import percentile as _percentile
from app.inference.verification_config import get_verification_config

from .report import ConsistencyPair, ConsistencyReport, FalseStats
from .statistics import (
    grading_semantic_equivalent,
    text_consistency_with_grading,
    text_similarity,
    wilson_confidence_interval,
)


def compute_organ_breakdown(report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
    organs: dict[str, list[ConsistencyPair]] = {}
    for p in pairs:
        organs.setdefault(p.organ, []).append(p)

    for organ_name, organ_pairs in organs.items():
        organ_total = len(organ_pairs)
        organ_consistent = sum(1 for p in organ_pairs if p.grading_semantic_consistent)
        organ_boundary = sum(1 for p in organ_pairs if p.is_boundary_crossing)
        organ_halluc_a = sum(1 for p in organ_pairs if p.engine_a_result and p.engine_a_result.is_hallucination)
        organ_halluc_b = sum(1 for p in organ_pairs if p.engine_b_result and p.engine_b_result.is_hallucination)
        organ_latencies_a = [
            p.engine_a_result.latency_ms for p in organ_pairs
            if p.engine_a_result and p.engine_a_result.error is None
        ]
        organ_latencies_b = [
            p.engine_b_result.latency_ms for p in organ_pairs
            if p.engine_b_result and p.engine_b_result.error is None
        ]
        organ_latency_a_p50 = _percentile(organ_latencies_a, 50) if organ_latencies_a else 0.0
        organ_latency_a_p99 = _percentile(organ_latencies_a, 99) if organ_latencies_a else 0.0
        organ_latency_b_p50 = _percentile(organ_latencies_b, 50) if organ_latencies_b else 0.0
        organ_latency_b_p99 = _percentile(organ_latencies_b, 99) if organ_latencies_b else 0.0
        _verification_config = get_verification_config()
        organ_latency_p99_reliable = len(organ_latencies_a) >= _verification_config.consistency_thresholds.latency_p99_min_samples and len(organ_latencies_b) >= _verification_config.consistency_thresholds.latency_p99_min_samples

        grading_distribution: dict[str, int] = {}
        for p in organ_pairs:
            grade = None
            if p.engine_a_result and p.engine_a_result.grading:
                grade = p.engine_a_result.grading
            elif p.engine_b_result and p.engine_b_result.grading:
                grade = p.engine_b_result.grading
            if grade:
                grading_distribution[grade] = grading_distribution.get(grade, 0) + 1

        organ_semantic_consistent_count = sum(
            1 for p in organ_pairs
            if p.engine_a_result and p.engine_b_result
            and grading_semantic_equivalent(p.engine_a_result.grading, p.engine_b_result.grading, p.organ)
        )

        organ_exact_match = sum(
            1 for p in organ_pairs
            if p.engine_a_result and p.engine_b_result
            and p.engine_a_result.text.strip() == p.engine_b_result.text.strip()
        )

        report.organ_breakdown[organ_name] = {
            "total": organ_total,
            "grading_consistent": organ_consistent,
            "grading_consistency_rate": organ_consistent / organ_total if organ_total > 0 else 0.0,
            "clinical_boundary_diff": organ_boundary,
            "clinical_boundary_diff_rate": organ_boundary / organ_total if organ_total > 0 else 0.0,
            "clinical_boundary_diff_rate_ci_lower": wilson_confidence_interval(organ_boundary, organ_total)[0] if organ_total > 0 else 0.0,
            "clinical_boundary_diff_rate_ci_upper": wilson_confidence_interval(organ_boundary, organ_total)[1] if organ_total > 0 else 0.0,
            "hallucination_a_count": organ_halluc_a,
            "hallucination_b_count": organ_halluc_b,
            "hallucination_rate_a": organ_halluc_a / organ_total if organ_total > 0 else 0.0,
            "hallucination_rate_b": organ_halluc_b / organ_total if organ_total > 0 else 0.0,
            "hallucination_rate_diff": abs(
                organ_halluc_a / organ_total - organ_halluc_b / organ_total
            ) if organ_total > 0 else 0.0,
            "text_exact_match_rate": organ_exact_match / organ_total if organ_total > 0 else 0.0,
            "text_consistency_rate": sum(
                1 for p in organ_pairs
                if p.engine_a_result and p.engine_b_result
                and text_consistency_with_grading(
                    p.engine_a_result.text, p.engine_b_result.text,
                    p.engine_a_result.grading, p.engine_b_result.grading,
                    p.organ,
                )
            ) / organ_total if organ_total > 0 else 0.0,
            "clinical_consistency_rate": sum(
                1 for p in organ_pairs
                if p.engine_a_result and p.engine_b_result
                and p.grading_semantic_consistent
                and not p.engine_a_result.is_hallucination
                and not p.engine_b_result.is_hallucination
            ) / organ_total if organ_total > 0 else 0.0,
            "latency_a_p50_ms": organ_latency_a_p50,
            "latency_a_p99_ms": organ_latency_a_p99,
            "latency_b_p50_ms": organ_latency_b_p50,
            "latency_b_p99_ms": organ_latency_b_p99,
            "latency_p99_reliable": organ_latency_p99_reliable,
            "latency_sample_count_a": len(organ_latencies_a),
            "latency_sample_count_b": len(organ_latencies_b),
            "latency_diff_pct": (
                abs(organ_latency_a_p99 - organ_latency_b_p99) / ((organ_latency_a_p99 + organ_latency_b_p99) / 2) * 100
                if organ_latency_a_p99 > 0 and organ_latency_b_p99 > 0 else 0.0
            ),
            "latency_diff_abs_ms": abs(organ_latency_a_p99 - organ_latency_b_p99),
            "grading_distribution": grading_distribution,
            "semantic_grading_consistency_rate": organ_semantic_consistent_count / organ_total if organ_total > 0 else 0.0,
            "semantic_grading_consistency_rate_ci_lower": wilson_confidence_interval(organ_semantic_consistent_count, organ_total)[0] if organ_total > 0 else 0.0,
            "semantic_grading_consistency_rate_ci_upper": wilson_confidence_interval(organ_semantic_consistent_count, organ_total)[1] if organ_total > 0 else 0.0,
            "text_similarity_avg": sum(
                text_similarity(p.engine_a_result.text, p.engine_b_result.text)
                for p in organ_pairs
                if p.engine_a_result and p.engine_b_result
            ) / organ_total if organ_total > 0 else 0.0,
        }

        organ_fp_a, organ_fn_a, organ_tp_a, organ_tn_a = 0, 0, 0, 0
        organ_fp_b, organ_fn_b, organ_tp_b, organ_tn_b = 0, 0, 0, 0
        organ_gt = 0
        for p in organ_pairs:
            if p.expected_hallucination is None:
                continue
            organ_gt += 1
            a_halluc = p.engine_a_result.is_hallucination if p.engine_a_result else False
            b_halluc = p.engine_b_result.is_hallucination if p.engine_b_result else False
            if a_halluc and p.expected_hallucination:
                organ_tp_a += 1
            elif a_halluc and not p.expected_hallucination:
                organ_fp_a += 1
            elif not a_halluc and p.expected_hallucination:
                organ_fn_a += 1
            else:
                organ_tn_a += 1
            if b_halluc and p.expected_hallucination:
                organ_tp_b += 1
            elif b_halluc and not p.expected_hallucination:
                organ_fp_b += 1
            elif not b_halluc and p.expected_hallucination:
                organ_fn_b += 1
            else:
                organ_tn_b += 1

        report.organ_false_stats[organ_name] = FalseStats(
            false_positives=organ_fp_a + organ_fp_b,
            false_negatives=organ_fn_a + organ_fn_b,
            true_positives=organ_tp_a + organ_tp_b,
            true_negatives=organ_tn_a + organ_tn_b,
            cases_with_ground_truth=organ_gt,
            engine_a_stats=FalseStats(
                false_positives=organ_fp_a,
                false_negatives=organ_fn_a,
                true_positives=organ_tp_a,
                true_negatives=organ_tn_a,
                cases_with_ground_truth=organ_gt,
            ),
            engine_b_stats=FalseStats(
                false_positives=organ_fp_b,
                false_negatives=organ_fn_b,
                true_positives=organ_tp_b,
                true_negatives=organ_tn_b,
                cases_with_ground_truth=organ_gt,
            ),
        )
