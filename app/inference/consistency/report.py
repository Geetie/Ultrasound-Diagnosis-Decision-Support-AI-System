from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.inference.hallucination_detector import detect_hallucination as unified_detect_hallucination
from app.inference.verification_config import VerificationConfig, get_verification_config

from .statistics import (
    _grades_cross_clinical_boundary,
    grading_clinically_equivalent,
    grading_semantic_equivalent,
)


class ConsistencyMetric(str, Enum):
    GRADING_CONSISTENCY = "grading_consistency"
    CLINICAL_BOUNDARY_DIFF = "clinical_boundary_diff"
    HALLUCINATION_RATE_DIFF = "hallucination_rate_diff"
    LATENCY_DIFF_PCT = "latency_diff_pct"


class GateStatus(str, Enum):
    PASSED = "passed"
    CONDITIONAL = "conditional"
    FAILED = "failed"


@dataclass
class GradingResult:
    text: str
    grading: str | None = None
    organ: str = ""
    latency_ms: float = 0.0
    is_hallucination: bool = False
    hallucination_issues: list[dict[str, Any]] = field(default_factory=list)
    has_lvef_description_conflict: bool = False
    error: str | None = None


@dataclass
class ConsistencyPair:
    case_id: str
    organ: str
    prompt: str
    expected_grading: str | None = None
    expected_hallucination: bool | None = None
    is_high_risk: bool = False
    engine_a_result: GradingResult | None = None
    engine_b_result: GradingResult | None = None
    boundary_weight: float = 0.0
    is_critical_boundary: bool = False

    @property
    def grading_consistent(self) -> bool:
        if self.engine_a_result is None or self.engine_b_result is None:
            return False
        if self.engine_a_result.grading is None or self.engine_b_result.grading is None:
            return False
        return self.engine_a_result.grading == self.engine_b_result.grading

    @property
    def grading_semantic_consistent(self) -> bool:
        if self.engine_a_result is None or self.engine_b_result is None:
            return False
        if self.engine_a_result.grading is None or self.engine_b_result.grading is None:
            return False
        return grading_semantic_equivalent(
            self.engine_a_result.grading, self.engine_b_result.grading, self.organ
        )

    @property
    def grading_clinically_consistent(self) -> bool:
        if self.engine_a_result is None or self.engine_b_result is None:
            return False
        if self.engine_a_result.grading is None or self.engine_b_result.grading is None:
            return False
        return grading_clinically_equivalent(
            self.engine_a_result.grading, self.engine_b_result.grading, self.organ
        )

    @property
    def has_extraction_conflict(self) -> bool:
        if self.engine_a_result and self.engine_a_result.has_lvef_description_conflict:
            return True
        if self.engine_b_result and self.engine_b_result.has_lvef_description_conflict:
            return True
        return False

    @property
    def is_boundary_crossing(self) -> bool:
        if not self.engine_a_result or not self.engine_b_result:
            return False
        if self.engine_a_result.grading is None or self.engine_b_result.grading is None:
            return False
        if self.engine_a_result.grading == self.engine_b_result.grading:
            return False
        return _grades_cross_clinical_boundary(
            self.engine_a_result.grading,
            self.engine_b_result.grading,
            self.organ,
        )

    @property
    def boundary_severity(self) -> str:
        if not self.is_boundary_crossing:
            return "none"
        if self.is_critical_boundary:
            return "critical"
        if self.boundary_weight >= 1.0:
            return "warning"
        return "low"

    @property
    def expected_vs_actual_boundary_crossing(self) -> bool:
        if not self.expected_grading:
            return False
        if not self.engine_a_result or not self.engine_b_result:
            return False
        for result in (self.engine_a_result, self.engine_b_result):
            if result.grading is None:
                continue
            if result.grading != self.expected_grading:
                if _grades_cross_clinical_boundary(self.expected_grading, result.grading, self.organ):
                    return True
        return False


@dataclass
class StrategyFalseStats:
    strategy: str
    false_positives: int = 0
    false_negatives: int = 0
    true_positives: int = 0
    true_negatives: int = 0
    engine_a_only_positives: int = 0
    engine_b_only_positives: int = 0


@dataclass
class FalseStats:
    false_positives: int = 0
    false_negatives: int = 0
    true_positives: int = 0
    true_negatives: int = 0
    cases_with_ground_truth: int = 0
    engine_a_stats: Optional["FalseStats"] = None
    engine_b_stats: Optional["FalseStats"] = None
    strategy_breakdown: dict[str, StrategyFalseStats] = field(default_factory=dict)
    precision_ci_lower: float = 0.0
    precision_ci_upper: float = 0.0
    recall_ci_lower: float = 0.0
    recall_ci_upper: float = 0.0
    f1_ci_lower: float = 0.0
    f1_ci_upper: float = 0.0

    @property
    def precision(self) -> float:
        if self.true_positives + self.false_positives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        if self.true_positives + self.false_negatives == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_negatives)

    @property
    def f1_score(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * (self.precision * self.recall) / (self.precision + self.recall)

    @property
    def has_valid_precision(self) -> bool:
        return (self.true_positives + self.false_positives) > 0

    @property
    def has_valid_recall(self) -> bool:
        return (self.true_positives + self.false_negatives) > 0


@dataclass
class CrossValidationResult:
    fold: int
    train_count: int
    validation_count: int
    train_accuracy: float
    validation_accuracy: float
    train_hallucination_rate: float
    validation_hallucination_rate: float
    accuracy_diff: float
    hallucination_rate_diff: float
    train_boundary_diff_rate: float = 0.0
    validation_boundary_diff_rate: float = 0.0
    boundary_diff_rate_diff: float = 0.0


@dataclass
class ConsistencyReport:
    engine_a_name: str
    engine_b_name: str
    total_cases: int = 0
    grading_consistent_count: int = 0
    grading_consistency_rate: float = 0.0
    clinical_boundary_diff_count: int = 0
    clinical_boundary_diff_rate: float = 0.0
    boundary_crossing_rate: float = 0.0
    clinical_boundary_diff_rate_ci_lower: float = 0.0
    clinical_boundary_diff_rate_ci_upper: float = 0.0
    total_boundary_weight: float = 0.0
    critical_boundary_diff_count: int = 0
    critical_boundary_diff_rate: float = 0.0
    weighted_boundary_diff_rate: float = 0.0
    hallucination_a_count: int = 0
    hallucination_b_count: int = 0
    hallucination_rate_diff: float = 0.0
    text_consistency_rate: float = 0.0
    text_consistency_rate_strict: float = 0.0
    text_exact_match_rate: float = 0.0
    clinical_consistency_rate: float = 0.0
    semantic_grading_consistency_rate: float = 0.0
    clinical_grading_consistency_rate: float = 0.0
    text_similarity_avg: float = 0.0
    latency_a_p50_ms: float = 0.0
    latency_a_p99_ms: float = 0.0
    latency_b_p50_ms: float = 0.0
    latency_b_p99_ms: float = 0.0
    latency_diff_pct: float = 0.0
    latency_diff_p50_pct: float = 0.0
    latency_sample_count_a: int = 0
    latency_sample_count_b: int = 0
    latency_p99_reliable: bool = False
    latency_faster_engine: str = ""
    organ_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    boundary_cases: list[dict[str, Any]] = field(default_factory=list)
    inconsistent_cases: list[dict[str, Any]] = field(default_factory=list)
    false_stats: FalseStats = field(default_factory=FalseStats)
    organ_false_stats: dict[str, FalseStats] = field(default_factory=dict)
    organ_sample_warnings: list[dict[str, Any]] = field(default_factory=list)
    organ_gate_failures: list[dict[str, Any]] = field(default_factory=list)
    failure_analysis: dict[str, Any] = field(default_factory=dict)
    test_data_version: str = ""
    elapsed_seconds: float = 0.0
    phase_upgraded: bool = False
    phase_upgrade_from: str = ""
    phase_upgrade_to: str = ""
    verification_temperature: float = 0.0
    lvef_conflict_count: int = 0
    hallucination_rate_a: float = 0.0
    hallucination_rate_b: float = 0.0
    hallucination_higher_engine: str = ""
    hallucination_rate_a_gt: float = 0.0
    hallucination_rate_b_gt: float = 0.0
    hallucination_rate_diff_gt: float = 0.0
    quantization_hallucination_increment: float = 0.0
    quantization_baseline_rate: float = 0.0
    quantization_test_rate: float = 0.0
    quantization_increment_exceeded: bool = False
    accuracy_retention_rate: float = 0.0
    strategy_hallucination_diff: dict[str, float] = field(default_factory=dict)
    hallucination_diff_p_value: float = 0.0
    absolute_hallucination_rate_exceeded: bool = False
    data_source_type: str = ""
    high_risk_hallucination_rate: float = 0.0
    high_risk_case_count: int = 0
    _config: VerificationConfig | None = field(default=None, repr=False)
    _gate_status_cache: GateStatus | None = field(default=None, repr=False)

    @property
    def gate_status(self) -> GateStatus:
        if self._gate_status_cache is not None:
            return self._gate_status_cache
        config = self._config or get_verification_config()
        thresholds = config.consistency_thresholds

        if thresholds.verification_phase == "phase2":
            for organ_name, organ_data in self.organ_breakdown.items():
                if organ_data.get("total", 0) < thresholds.phase2_min_cases_per_organ:
                    return GateStatus.FAILED

        gc_threshold = thresholds.grading_consistency_rate
        if thresholds.verification_phase == "phase1":
            bd_threshold = thresholds.phase1_clinical_boundary_diff_rate
        else:
            bd_threshold = thresholds.clinical_boundary_diff_rate

        if config.adaptive_thresholds:
            from app.inference.verification_config import compute_adaptive_threshold
            for at in config.adaptive_thresholds:
                if at.metric_name == "grading_consistency_rate":
                    historical = getattr(config, "_historical_pass_rates", {}).get("grading_consistency_rate", [])
                    gc_threshold = compute_adaptive_threshold(at, historical)
                elif at.metric_name == "clinical_boundary_diff_rate":
                    historical = getattr(config, "_historical_pass_rates", {}).get("clinical_boundary_diff_rate", [])
                    phase_base = thresholds.phase1_clinical_boundary_diff_rate if thresholds.verification_phase == "phase1" else thresholds.clinical_boundary_diff_rate
                    phase_at = at.model_copy(update={"base_threshold": phase_base})
                    bd_threshold = compute_adaptive_threshold(phase_at, historical)

        gc = self.grading_consistency_rate
        sgc = self.semantic_grading_consistency_rate
        bd = self.clinical_boundary_diff_rate
        bd_ci_upper = self.clinical_boundary_diff_rate_ci_upper
        wbd = self.weighted_boundary_diff_rate
        cbd = self.critical_boundary_diff_rate
        hd = self.hallucination_rate_diff
        tc = self.text_consistency_rate
        ld = self.latency_diff_pct
        ld_abs = abs(self.latency_a_p99_ms - self.latency_b_p99_ms)
        if not self.latency_faster_engine and self.latency_a_p99_ms != self.latency_b_p99_ms:
            if self.latency_a_p99_ms < self.latency_b_p99_ms:
                self.latency_faster_engine = self.engine_a_name
            else:
                self.latency_faster_engine = self.engine_b_name
        lvef_rate = self.lvef_conflict_count / self.total_cases if self.total_cases > 0 else 0.0
        ld_passed: bool
        if not self.latency_p99_reliable:
            ld_passed = False
        elif thresholds.latency_diff_gate_logic == "and":
            ld_passed = ld <= thresholds.latency_diff_pct and ld_abs <= thresholds.latency_diff_abs_ms
        else:
            ld_passed = ld <= thresholds.latency_diff_pct or ld_abs <= thresholds.latency_diff_abs_ms

        ld_abs_passed = (ld_abs <= thresholds.latency_diff_abs_ms) if self.latency_p99_reliable else False
        ld_conditional = (ld <= thresholds.latency_diff_pct * thresholds.conditional_latency_diff_pct_multiplier) if self.latency_p99_reliable else True

        organ_failures: list[dict[str, Any]] = []
        for organ_name, organ_data in self.organ_breakdown.items():
            organ_gc = organ_data.get("grading_consistency_rate", 1.0)
            organ_bd = organ_data.get("clinical_boundary_diff_rate", 0.0)
            if organ_gc < gc_threshold or organ_bd > bd_threshold:
                organ_failures.append({
                    "organ": organ_name,
                    "grading_consistency_rate": organ_gc,
                    "clinical_boundary_diff_rate": organ_bd,
                    "grading_threshold": gc_threshold,
                    "boundary_threshold": bd_threshold,
                })
        self.organ_gate_failures = organ_failures

        has_organ_failure = any(
            f.get("grading_consistency_rate", 1.0) < thresholds.conditional_grading_consistency_rate
            or f.get("clinical_boundary_diff_rate", 0.0) > thresholds.conditional_clinical_boundary_diff_rate
            for f in organ_failures
        )
        has_organ_conditional = len(organ_failures) > 0 and not has_organ_failure

        if has_organ_failure:
            self._gate_status_cache = GateStatus.FAILED
            return GateStatus.FAILED

        abs_halluc_threshold = thresholds.absolute_hallucination_rate_threshold
        abs_halluc_exceeded = (
            self.hallucination_rate_a > abs_halluc_threshold
            or self.hallucination_rate_b > abs_halluc_threshold
        )
        self.absolute_hallucination_rate_exceeded = abs_halluc_exceeded

        bd_ci_check = True
        if thresholds.verification_phase == "phase2":
            bd_ci_check = bd_ci_upper <= thresholds.phase2_boundary_ci_upper_threshold

        if (gc >= gc_threshold
                and sgc >= thresholds.semantic_grading_consistency_rate
                and bd <= bd_threshold
                and bd_ci_check
                and wbd <= thresholds.weighted_boundary_diff_rate
                and cbd <= thresholds.critical_boundary_diff_rate
                and hd <= thresholds.hallucination_rate_diff
                and tc >= thresholds.text_consistency_rate
                and ld_passed
                and ld_abs_passed
                and lvef_rate <= thresholds.lvef_conflict_rate_threshold
                and not has_organ_conditional
                and not abs_halluc_exceeded):
            self._gate_status_cache = GateStatus.PASSED
            return GateStatus.PASSED
        if (gc >= thresholds.conditional_grading_consistency_rate
                and bd_ci_upper <= thresholds.conditional_clinical_boundary_diff_rate
                and hd <= thresholds.hallucination_rate_diff * thresholds.conditional_hallucination_rate_diff_multiplier
                and tc >= thresholds.conditional_text_consistency_rate
                and ld_conditional):
            self._gate_status_cache = GateStatus.CONDITIONAL
            return GateStatus.CONDITIONAL
        self._gate_status_cache = GateStatus.FAILED
        return GateStatus.FAILED

    @property
    def passed(self) -> bool:
        return self.gate_status == GateStatus.PASSED

    @property
    def remediation(self) -> dict[str, Any]:
        if self.gate_status == GateStatus.PASSED:
            return {"action": "proceed", "details": "所有指标达标"}

        thresholds = (self._config or get_verification_config()).consistency_thresholds
        deviations: dict[str, Any] = {}

        gc_dev = (thresholds.grading_consistency_rate - self.grading_consistency_rate) / thresholds.grading_consistency_rate * 100
        if gc_dev > 0:
            deviations["grading_consistency"] = {
                "deviation_pct": round(gc_dev, 2),
                "action": "optimize_and_retest" if gc_dev <= 10 else "downgrade_plan",
            }

        bd_dev = (self.clinical_boundary_diff_rate - thresholds.clinical_boundary_diff_rate) / thresholds.clinical_boundary_diff_rate * 100
        if bd_dev > 0:
            deviations["clinical_boundary_diff"] = {
                "deviation_pct": round(bd_dev, 2),
                "action": "optimize_and_retest" if bd_dev <= 10 else "downgrade_plan",
            }

        hd_dev = (self.hallucination_rate_diff - thresholds.hallucination_rate_diff) / thresholds.hallucination_rate_diff * 100
        if hd_dev > 0:
            deviations["hallucination_rate_diff"] = {
                "deviation_pct": round(hd_dev, 2),
                "action": "optimize_and_retest" if hd_dev <= 10 else "downgrade_plan",
            }

        tc_dev = (thresholds.text_consistency_rate - self.text_consistency_rate) / thresholds.text_consistency_rate * 100
        if tc_dev > 0:
            deviations["text_consistency"] = {
                "deviation_pct": round(tc_dev, 2),
                "action": "optimize_and_retest" if tc_dev <= 10 else "downgrade_plan",
            }

        ld_dev = (self.latency_diff_pct - thresholds.latency_diff_pct) / thresholds.latency_diff_pct * 100
        if ld_dev > 0:
            deviations["latency_diff_pct"] = {
                "deviation_pct": round(ld_dev, 2),
                "action": "optimize_and_retest" if ld_dev <= 10 else "downgrade_plan",
            }

        if thresholds.text_exact_match_rate > 0:
            tem_dev = (thresholds.text_exact_match_rate - self.text_exact_match_rate) / thresholds.text_exact_match_rate * 100
            if tem_dev > 0:
                deviations["text_exact_match_rate"] = {
                    "deviation_pct": round(tem_dev, 2),
                    "action": "reference_only",
                    "detail": "文本精确匹配率为参考指标，不影响Gate判定",
                }

        if not self.latency_p99_reliable:
            config_rem = self._config or get_verification_config()
            min_samples = config_rem.consistency_thresholds.latency_p99_min_samples
            deviations["latency_reliability"] = {
                "deviation_pct": 0,
                "action": "insufficient_samples",
                "detail": f"延迟样本不足(A={self.latency_sample_count_a}, B={self.latency_sample_count_b}, 需各≥{min_samples})",
            }

        return {"action": "remediation_required", "deviations": deviations}

    @property
    def nmpa_declaration(self) -> dict[str, Any] | None:
        if self.gate_status != GateStatus.CONDITIONAL:
            return None
        thresholds = (self._config or get_verification_config()).consistency_thresholds
        data_source_note = ""
        if self.data_source_type == "public":
            data_source_note = "基于公开数据集，院内数据验证待Gate 0B补充。"
        elif self.data_source_type == "hospital":
            data_source_note = "基于院内标注数据。"
        return {
            "framework_a": self.engine_a_name,
            "framework_b": self.engine_b_name,
            "grading_consistency_rate": f"{self.grading_consistency_rate:.2%}",
            "grading_threshold": f"{thresholds.grading_consistency_rate:.0%}",
            "boundary_diff_rate": f"{self.clinical_boundary_diff_rate:.2%}",
            "boundary_threshold": f"{thresholds.clinical_boundary_diff_rate:.0%}",
            "hallucination_rate_diff": f"{self.hallucination_rate_diff:.2%}",
            "hallucination_rate_a": f"{self.hallucination_rate_a:.2%}",
            "hallucination_rate_b": f"{self.hallucination_rate_b:.2%}",
            "absolute_hallucination_exceeded": self.absolute_hallucination_rate_exceeded,
            "data_source_type": self.data_source_type,
            "data_source_note": data_source_note,
            "declaration": (
                f"本产品使用{self.engine_a_name}与{self.engine_b_name}双推理框架，"
                f"分级一致率{self.grading_consistency_rate:.2%}，"
                f"临床边界差异率{self.clinical_boundary_diff_rate:.2%}，"
                f"幻觉率差异{self.hallucination_rate_diff:.2%}，"
                f"符合条件性通过标准。前端已标注当前使用的推理框架类型。"
                f"{data_source_note}"
            ),
        }


def detect_hallucination(text: str, organ: str) -> bool:
    return unified_detect_hallucination(text, organ)
