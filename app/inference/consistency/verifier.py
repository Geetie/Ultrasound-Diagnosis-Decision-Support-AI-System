import asyncio
import time
from typing import Any

import structlog

from app.inference.grading_definitions import extract_grading_with_conflict
from app.inference.hallucination_detector import (
    DetectionStrategy,
    get_default_detector,
)
from app.inference.protocols import InferenceEngine, InferenceRequest
from app.inference.utils import percentile as _percentile
from app.inference.verification_config import (
    VerificationConfig,
    get_verification_config,
)

from .failure_analysis import generate_failure_analysis
from .organ_breakdown import compute_organ_breakdown
from .report import (
    ConsistencyPair,
    ConsistencyReport,
    CrossValidationResult,
    FalseStats,
    GateStatus,
    GradingResult,
    StrategyFalseStats,
)
from .serialization import report_to_dict, report_to_json
from .statistics import (
    grading_clinically_equivalent,
    grading_semantic_equivalent,
    text_consistency_with_grading,
    text_similarity,
    wilson_confidence_interval,
)

logger = structlog.get_logger("app.inference.consistency")

# 结构化输出指令段（2026-09-29 真机 780 例达标攻关）：两引擎收到**完全相同**的
# 指令文本，要求末行以 "分级: <值>" 收束——抽取器（grading_definitions）对该
# 行有专用模式，正则兜底仍在。格式随器官的分级体系而定（与 07 号文档口径一致）。
_STRUCTURED_OUTPUT_INSTRUCTIONS: dict[str, str] = {
    "thyroid": (
        "\n\n输出要求：先给出简要依据（不超过3条），最后必须以单独一行"
        "`分级: TI-RADS X` 结束（X 为 2/3/4a/4b/5 之一），不要输出其他分级体系。"
    ),
    "liver": (
        "\n\n输出要求：先给出简要依据（不超过3条），最后必须以单独一行"
        "`分级: LI-RADS X` 结束（X 为 1/2/3/4/5 之一），不要输出其他分级体系。"
    ),
    "cardiac": (
        "\n\n输出要求：先给出简要依据（不超过3条），最后必须以单独一行"
        "`分级: X` 结束（X 为 normal/mild/moderate/severe 之一，与 LVEF 阈值"
        " ≥55/≥45/≥30 对应），不要输出其他分级体系。"
    ),
}

# 器官名兜底：未知器官不加指令段（保持裸 prompt，抽取失败会如实计 extraction_failure）
_STRUCTURED_OUTPUT_FALLBACK = (
    "\n\n输出要求：先给出简要依据（不超过3条），最后必须以单独一行"
    "`分级: <分级结论>` 结束。"
)


class ConsistencyVerifier:
    def __init__(
        self,
        engine_a: InferenceEngine,
        engine_b: InferenceEngine,
        engine_a_name: str = "engine_a",
        engine_b_name: str = "engine_b",
        concurrency: int = 4,
        request_timeout_seconds: float = 30.0,
        config: VerificationConfig | None = None,
        historical_pass_rates: dict[str, list[float]] | None = None,
        overall_timeout_seconds: float | None = None,
        structured_output: bool = False,
    ):
        self._engine_a = engine_a
        self._engine_b = engine_b
        self._engine_a_name = engine_a_name
        self._engine_b_name = engine_b_name
        self._concurrency = concurrency
        self._request_timeout_seconds = request_timeout_seconds
        self._overall_timeout_seconds = overall_timeout_seconds
        self.config = config or get_verification_config()
        self._historical_pass_rates = historical_pass_rates or {}
        self._hallucination_detector = get_default_detector()
        # 结构化输出（2026-09-29 真机 780 例达标攻关）：两引擎收到完全相同的
        # 输出格式指令段，消除"自由文本分级"的表述噪音。默认关闭保持兼容。
        self._structured_output = structured_output
        # 01-4 删净（2026-10-31）：guided_json 链路整体移除 —— 新版 vLLM 对
        # extra["guided_json"] 是「静默忽略」（AGENTS.md 硬规则 #5），这条链
        # 会造成"以为采样层已强制、实际毫无约束"的假象；生产 0 构造、测试 0
        # 引用。结构化输出唯一路径 = response_format（vLLM）+ prompt 指令段。

        logger.info(
            "consistency_verifier_initialized",
            engine_a=engine_a_name,
            engine_b=engine_b_name,
            concurrency=concurrency,
            test_data_version=self.config.test_data_version,
            structured_output=structured_output,
        )

    async def verify(
        self,
        cases: list[dict[str, Any]],
    ) -> ConsistencyReport:
        start_time = time.monotonic()
        request_temperature = 0.0
        report = ConsistencyReport(
            engine_a_name=self._engine_a_name,
            engine_b_name=self._engine_b_name,
            test_data_version=self.config.test_data_version,
            verification_temperature=request_temperature,
            structured_output=self._structured_output,
            _config=self.config,
        )

        logger.info(
            "consistency_verification_started",
            case_count=len(cases),
            test_data_version=report.test_data_version,
            temperature=request_temperature,
        )

        semaphore = asyncio.Semaphore(self._concurrency)

        async def _run_case(case: dict[str, Any]) -> ConsistencyPair:
            async with semaphore:
                return await self._verify_single_case(case)

        tasks = [_run_case(c) for c in cases]

        if self._overall_timeout_seconds is not None:
            try:
                pairs = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._overall_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "consistency_verification_overall_timeout",
                    timeout=self._overall_timeout_seconds,
                    case_count=len(cases),
                )
                pairs = []
        else:
            pairs = await asyncio.gather(*tasks, return_exceptions=True)

        valid_pairs: list[ConsistencyPair] = []
        error_count = 0
        for p in pairs:
            if isinstance(p, Exception):
                logger.error("case_verification_failed", error=str(p))
                error_count += 1
            else:
                valid_pairs.append(p)

        self._compute_report(report, valid_pairs, error_count)

        # 逐例摘要行（T0 原口径重算与"预期 vs 实际"检测需要全量逐例数据；
        # trace_chain 只含失败例，这里补全量紧凑行。字段见 report_to_dict。）
        report.case_rows = [
            {
                "case_id": p.case_id,
                "organ": p.organ,
                "expected_grading": p.expected_grading,
                "grading_a": p.engine_a_result.grading if p.engine_a_result else None,
                "grading_b": p.engine_b_result.grading if p.engine_b_result else None,
                "consistent": p.grading_consistent,
                "is_hallucination_a": p.engine_a_result.is_hallucination if p.engine_a_result else None,
                "is_hallucination_b": p.engine_b_result.is_hallucination if p.engine_b_result else None,
                "category": p.category,
            }
            for p in valid_pairs
        ]

        min_cases = self.config.consistency_thresholds.phase2_min_cases_per_organ \
            if self.config.consistency_thresholds.verification_phase == "phase2" else 100

        organ_counts: dict[str, int] = {}
        for p in valid_pairs:
            organ_counts[p.organ] = organ_counts.get(p.organ, 0) + 1
        all_organs_meet_phase2 = all(
            count >= self.config.consistency_thresholds.phase2_min_cases_per_organ
            for count in organ_counts.values()
        ) if organ_counts else False
        if all_organs_meet_phase2 and self.config.consistency_thresholds.verification_phase == "phase1":
            if self.config.consistency_thresholds.auto_upgrade_phase:
                original_phase = self.config.consistency_thresholds.verification_phase
                self.config.consistency_thresholds.verification_phase = "phase2"
                min_cases = self.config.consistency_thresholds.phase2_min_cases_per_organ
                report.phase_upgraded = True
                report.phase_upgrade_from = "phase1"
                report.phase_upgrade_to = "phase2"
                logger.info("verification_phase_auto_upgraded", phase="phase2")
                if report.gate_status == GateStatus.FAILED:
                    self.config.consistency_thresholds.verification_phase = original_phase
                    report.phase_upgraded = False
                    report.phase_upgrade_from = ""
                    report.phase_upgrade_to = ""
                    min_cases = 100
                    logger.info("verification_phase_rolled_back", reason="phase2_gate_failed", original_phase=original_phase)
            else:
                logger.info("phase_upgrade_skipped", reason="auto_upgrade_phase_disabled")

        for organ_name, organ_data in report.organ_breakdown.items():
            if organ_data.get("total", 0) < min_cases:
                report.organ_sample_warnings.append({
                    "organ": organ_name,
                    "actual": organ_data["total"],
                    "required": min_cases,
                })
                logger.warning(
                    "insufficient_organ_samples",
                    organ=organ_name,
                    actual=organ_data["total"],
                    required=min_cases,
                )

        report.elapsed_seconds = time.monotonic() - start_time

        self._submit_to_drift_detector(report)

        logger.info(
            "consistency_verification_completed",
            total=report.total_cases,
            grading_consistency=f"{report.grading_consistency_rate:.2%}",
            boundary_diff=f"{report.clinical_boundary_diff_rate:.2%}",
            hallucination_diff=f"{report.hallucination_rate_diff:.2%}",
            passed=report.passed,
            elapsed=f"{report.elapsed_seconds:.1f}s",
            test_data_version=report.test_data_version,
        )

        return report

    def _apply_structured_output(self, prompt: str, organ: str) -> str:
        """按器官追加输出格式指令段（未启用时原样返回）。

        structured_output 关闭时不追加。
        """
        if not self._structured_output:
            return prompt
        suffix = _STRUCTURED_OUTPUT_INSTRUCTIONS.get(organ, _STRUCTURED_OUTPUT_FALLBACK)
        return prompt + suffix

    def _extract_grading(self, text: str, organ: str) -> tuple[str | None, bool]:
        """抽取分级：正则抽取器（含冲突检测）。"""
        conflict_result = extract_grading_with_conflict(text, organ)
        return (conflict_result[0], conflict_result[1]) if conflict_result else (None, False)

    async def _verify_single_case(self, case: dict[str, Any]) -> ConsistencyPair:
        case_id = case.get("case_id", "")
        organ = case.get("organ", "")
        prompt = case.get("prompt", "")
        expected_grading = case.get("expected_grading")
        expected_hallucination = case.get("expected_hallucination", False)
        is_high_risk = case.get("is_high_risk", False)

        pair = ConsistencyPair(
            case_id=case_id,
            organ=organ,
            prompt=prompt,
            expected_grading=expected_grading,
            expected_hallucination=expected_hallucination,
            is_high_risk=is_high_risk,
            category=case.get("category"),
        )

        request = InferenceRequest(
            prompt=self._apply_structured_output(prompt, organ),
            max_tokens=512,
            temperature=0.0,
            top_p=0.95,
            request_id=f"consistency-{case_id}",
            session_id="consistency-verification",
        )

        try:
            resp_a = await asyncio.wait_for(
                self._engine_a.generate(request),
                timeout=self._request_timeout_seconds,
            )
            grading_a, has_conflict_a = self._extract_grading(resp_a.text, organ)
            detection_result_a = self._hallucination_detector.detect(resp_a.text, organ)
            is_hallucination_a = detection_result_a.is_hallucination
            hallucination_issues_a = [
                {"strategy": issue.strategy.value if hasattr(issue.strategy, "value") else str(issue.strategy),
                 "severity": issue.severity,
                 "description": issue.description}
                for issue in detection_result_a.issues
            ]
            pair.engine_a_result = GradingResult(
                text=resp_a.text,
                grading=grading_a,
                organ=organ,
                latency_ms=resp_a.latency_ms,
                is_hallucination=is_hallucination_a,
                hallucination_issues=hallucination_issues_a,
                has_lvef_description_conflict=has_conflict_a,
            )
        except Exception as exc:
            pair.engine_a_result = GradingResult(
                text="",
                organ=organ,
                error=str(exc),
            )

        try:
            resp_b = await asyncio.wait_for(
                self._engine_b.generate(request),
                timeout=self._request_timeout_seconds,
            )
            grading_b, has_conflict_b = self._extract_grading(resp_b.text, organ)
            detection_result_b = self._hallucination_detector.detect(resp_b.text, organ)
            is_hallucination_b = detection_result_b.is_hallucination
            hallucination_issues_b = [
                {"strategy": issue.strategy.value if hasattr(issue.strategy, "value") else str(issue.strategy),
                 "severity": issue.severity,
                 "description": issue.description}
                for issue in detection_result_b.issues
            ]
            pair.engine_b_result = GradingResult(
                text=resp_b.text,
                grading=grading_b,
                organ=organ,
                latency_ms=resp_b.latency_ms,
                is_hallucination=is_hallucination_b,
                hallucination_issues=hallucination_issues_b,
                has_lvef_description_conflict=has_conflict_b,
            )
        except Exception as exc:
            pair.engine_b_result = GradingResult(
                text="",
                organ=organ,
                error=str(exc),
            )

        if expected_grading and pair.engine_a_result and pair.engine_b_result:
            self._compute_boundary_weight(pair, expected_grading, organ)
        elif pair.engine_a_result and pair.engine_b_result and pair.is_boundary_crossing:
            logger.warning(
                "boundary_crossing_without_expected_grading",
                case_id=case_id,
                organ=organ,
                grading_a=pair.engine_a_result.grading,
                grading_b=pair.engine_b_result.grading,
            )
            self._compute_boundary_weight_from_engines(pair, organ)

        return pair

    def _compute_boundary_weight(self, pair: ConsistencyPair, expected: str, organ: str):
        organ_boundaries = self.config.boundary_config.organ_boundaries.get(organ, [])
        max_weight = 0.0
        is_critical = False

        for boundary_config in organ_boundaries:
            if pair.engine_a_result and pair.engine_a_result.grading:
                if self._boundary_matches(boundary_config, expected, pair.engine_a_result.grading):
                    max_weight = max(max_weight, boundary_config.weight)
                    is_critical = is_critical or boundary_config.is_critical
            if pair.engine_b_result and pair.engine_b_result.grading:
                if self._boundary_matches(boundary_config, expected, pair.engine_b_result.grading):
                    max_weight = max(max_weight, boundary_config.weight)
                    is_critical = is_critical or boundary_config.is_critical

        grade_a = pair.engine_a_result.grading if pair.engine_a_result else None
        grade_b = pair.engine_b_result.grading if pair.engine_b_result else None
        if grade_a and grade_b and grade_a != grade_b:
            for boundary_config in organ_boundaries:
                if self._boundary_matches(boundary_config, grade_a, grade_b):
                    max_weight = max(max_weight, boundary_config.weight)
                    is_critical = is_critical or boundary_config.is_critical

        pair.boundary_weight = max_weight
        pair.is_critical_boundary = is_critical

    def _compute_boundary_weight_from_engines(self, pair: ConsistencyPair, organ: str):
        organ_boundaries = self.config.boundary_config.organ_boundaries.get(organ, [])
        max_weight = 0.0
        is_critical = False
        grade_a = pair.engine_a_result.grading if pair.engine_a_result else None
        grade_b = pair.engine_b_result.grading if pair.engine_b_result else None
        if not grade_a or not grade_b:
            return
        for boundary_config in organ_boundaries:
            if self._boundary_matches(boundary_config, grade_a, grade_b):
                max_weight = max(max_weight, boundary_config.weight)
                is_critical = is_critical or boundary_config.is_critical
        pair.boundary_weight = max_weight
        pair.is_critical_boundary = is_critical

    @staticmethod
    def _boundary_matches(boundary_config, from_grade: str, to_grade: str) -> bool:
        if boundary_config.from_grade == from_grade and boundary_config.to_grade == to_grade:
            return True
        if not boundary_config.is_directional:
            return boundary_config.from_grade == to_grade and boundary_config.to_grade == from_grade
        return False

    def _compute_report(
        self,
        report: ConsistencyReport,
        pairs: list[ConsistencyPair],
        error_count: int = 0,
    ) -> None:
        report.total_cases = len(pairs) + error_count
        if not pairs:
            return

        self._compute_basic_stats(report, pairs)
        self._compute_boundary_stats(report, pairs)
        self._compute_hallucination_stats(report, pairs)
        self._compute_text_stats(report, pairs)
        self._compute_false_stats(report, pairs)
        self._compute_latency_stats(report, pairs)
        compute_organ_breakdown(report, pairs)
        self._compute_anomaly_cases(report, pairs)
        report.failure_analysis = generate_failure_analysis(report, pairs)

    def _compute_basic_stats(self, report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
        grading_consistent = sum(1 for p in pairs if p.grading_consistent)
        report.grading_consistent_count = grading_consistent
        report.grading_consistency_rate = grading_consistent / len(pairs)

    def _compute_boundary_stats(self, report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
        boundary_diff = sum(1 for p in pairs if p.is_boundary_crossing)
        total_boundary_weight = sum(p.boundary_weight for p in pairs if p.is_boundary_crossing)
        critical_boundary_diff = sum(1 for p in pairs if p.is_boundary_crossing and p.boundary_severity == "critical")
        actual_boundary_weight_sum = 0.0
        for p in pairs:
            if p.is_boundary_crossing:
                actual_boundary_weight_sum += p.boundary_weight
        report.clinical_boundary_diff_count = boundary_diff
        report.clinical_boundary_diff_rate = boundary_diff / len(pairs)
        report.boundary_crossing_rate = boundary_diff / len(pairs)
        ci_lower, ci_upper = wilson_confidence_interval(boundary_diff, len(pairs))
        report.clinical_boundary_diff_rate_ci_lower = ci_lower
        report.clinical_boundary_diff_rate_ci_upper = ci_upper
        report.total_boundary_weight = total_boundary_weight
        report.critical_boundary_diff_count = critical_boundary_diff
        report.critical_boundary_diff_rate = critical_boundary_diff / len(pairs)
        report.weighted_boundary_diff_rate = (
            total_boundary_weight / actual_boundary_weight_sum
            if actual_boundary_weight_sum > 0 else 0.0
        )

    def _compute_hallucination_stats(self, report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
        halluc_a = sum(
            1 for p in pairs
            if p.engine_a_result and p.engine_a_result.is_hallucination
        )
        halluc_b = sum(
            1 for p in pairs
            if p.engine_b_result and p.engine_b_result.is_hallucination
        )
        report.hallucination_a_count = halluc_a
        report.hallucination_b_count = halluc_b
        rate_a = halluc_a / len(pairs) if pairs else 0
        rate_b = halluc_b / len(pairs) if pairs else 0
        report.hallucination_rate_diff = abs(rate_a - rate_b)
        report.hallucination_rate_a = rate_a
        report.hallucination_rate_b = rate_b
        if rate_a > rate_b:
            report.hallucination_higher_engine = self._engine_a_name
        elif rate_b > rate_a:
            report.hallucination_higher_engine = self._engine_b_name
        else:
            report.hallucination_higher_engine = ""

        lvef_conflicts = sum(
            1 for p in pairs
            if p.has_extraction_conflict
        )
        report.lvef_conflict_count = lvef_conflicts

        strategy_diff: dict[str, float] = {}
        for strategy in DetectionStrategy:
            a_count = sum(
                1 for p in pairs
                if p.engine_a_result and any(
                    i.get("strategy") == strategy.value
                    for i in p.engine_a_result.hallucination_issues
                )
            )
            b_count = sum(
                1 for p in pairs
                if p.engine_b_result and any(
                    i.get("strategy") == strategy.value
                    for i in p.engine_b_result.hallucination_issues
                )
            )
            if len(pairs) > 0:
                strategy_diff[strategy.value] = abs(a_count - b_count) / len(pairs)
        report.strategy_hallucination_diff = strategy_diff

        from .statistics import mcnemar_test
        a_halluc_flags = [
            bool(p.engine_a_result and p.engine_a_result.is_hallucination)
            for p in pairs
        ]
        b_halluc_flags = [
            bool(p.engine_b_result and p.engine_b_result.is_hallucination)
            for p in pairs
        ]
        _, p_val = mcnemar_test(a_halluc_flags, b_halluc_flags)
        report.hallucination_diff_p_value = p_val

        high_risk_pairs = [p for p in pairs if p.is_high_risk]
        if high_risk_pairs:
            hr_halluc = sum(
                1 for p in high_risk_pairs
                if (p.engine_a_result and p.engine_a_result.is_hallucination)
                or (p.engine_b_result and p.engine_b_result.is_hallucination)
            )
            report.high_risk_case_count = len(high_risk_pairs)
            report.high_risk_hallucination_rate = hr_halluc / len(high_risk_pairs)

        gt_pairs = [p for p in pairs if p.expected_hallucination is not None]
        if gt_pairs:
            gt_halluc_a = sum(
                1 for p in gt_pairs
                if p.engine_a_result and p.engine_a_result.is_hallucination
            )
            gt_halluc_b = sum(
                1 for p in gt_pairs
                if p.engine_b_result and p.engine_b_result.is_hallucination
            )
            report.hallucination_rate_a_gt = gt_halluc_a / len(gt_pairs)
            report.hallucination_rate_b_gt = gt_halluc_b / len(gt_pairs)
            report.hallucination_rate_diff_gt = abs(report.hallucination_rate_a_gt - report.hallucination_rate_b_gt)

    def _compute_text_stats(self, report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
        text_consistent = sum(
            1 for p in pairs
            if p.engine_a_result and p.engine_b_result
            and text_consistency_with_grading(
                p.engine_a_result.text, p.engine_b_result.text,
                p.engine_a_result.grading, p.engine_b_result.grading,
                p.organ,
            )
        )
        report.text_consistency_rate = text_consistent / len(pairs) if pairs else 0.0

        text_strict_consistent = sum(
            1 for p in pairs
            if p.engine_a_result and p.engine_b_result
            and text_similarity(p.engine_a_result.text, p.engine_b_result.text) >= 0.8
        )
        report.text_consistency_rate_strict = text_strict_consistent / len(pairs) if pairs else 0.0

        text_exact_match = sum(
            1 for p in pairs
            if p.engine_a_result and p.engine_b_result
            and p.engine_a_result.text.strip() == p.engine_b_result.text.strip()
        )
        report.text_exact_match_rate = text_exact_match / len(pairs) if pairs else 0.0

        clinical_consistent = sum(
            1 for p in pairs
            if p.engine_a_result and p.engine_b_result
            and p.grading_semantic_consistent
            and not p.engine_a_result.is_hallucination
            and not p.engine_b_result.is_hallucination
        )
        report.clinical_consistency_rate = clinical_consistent / len(pairs) if pairs else 0.0

        semantic_consistent = sum(
            1 for p in pairs
            if p.engine_a_result and p.engine_b_result
            and grading_semantic_equivalent(p.engine_a_result.grading, p.engine_b_result.grading, p.organ)
        )
        report.semantic_grading_consistency_rate = semantic_consistent / len(pairs) if pairs else 0.0

        clinical_consistent_grading = sum(
            1 for p in pairs
            if p.engine_a_result and p.engine_b_result
            and grading_clinically_equivalent(p.engine_a_result.grading, p.engine_b_result.grading, p.organ)
        )
        report.clinical_grading_consistency_rate = clinical_consistent_grading / len(pairs) if pairs else 0.0

        similarity_scores = [
            text_similarity(p.engine_a_result.text, p.engine_b_result.text)
            for p in pairs
            if p.engine_a_result and p.engine_b_result
        ]
        report.text_similarity_avg = sum(similarity_scores) / len(similarity_scores) if similarity_scores else 0.0

    def _compute_false_stats(self, report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
        a_fp, a_fn, a_tp, a_tn = 0, 0, 0, 0
        b_fp, b_fn, b_tp, b_tn = 0, 0, 0, 0
        cases_with_ground_truth = 0
        strategy_stats: dict[str, dict[str, int]] = {}

        for p in pairs:
            if p.expected_hallucination is None:
                continue

            a_halluc = p.engine_a_result and p.engine_a_result.is_hallucination
            b_halluc = p.engine_b_result and p.engine_b_result.is_hallucination

            cases_with_ground_truth += 1
            if not p.expected_hallucination:
                if a_halluc:
                    a_fp += 1
                else:
                    a_tn += 1
                if b_halluc:
                    b_fp += 1
                else:
                    b_tn += 1
            else:
                if a_halluc:
                    a_tp += 1
                else:
                    a_fn += 1
                if b_halluc:
                    b_tp += 1
                else:
                    b_fn += 1

            a_issues = p.engine_a_result.hallucination_issues if p.engine_a_result else []
            b_issues = p.engine_b_result.hallucination_issues if p.engine_b_result else []
            all_strategies = set()
            for issue in a_issues + b_issues:
                s = issue.get("strategy", "unknown")
                all_strategies.add(s)
            for s in all_strategies:
                if s not in strategy_stats:
                    strategy_stats[s] = {"fp": 0, "fn": 0, "tp": 0, "tn": 0, "a_only_pos": 0, "b_only_pos": 0}
                a_has = any(i.get("strategy") == s for i in a_issues)
                b_has = any(i.get("strategy") == s for i in b_issues)
                if a_has and not b_has:
                    strategy_stats[s]["a_only_pos"] += 1
                if b_has and not a_has:
                    strategy_stats[s]["b_only_pos"] += 1
                if not p.expected_hallucination:
                    if a_has or b_has:
                        strategy_stats[s]["fp"] += 1
                    else:
                        strategy_stats[s]["tn"] += 1
                else:
                    if a_has or b_has:
                        strategy_stats[s]["tp"] += 1
                    else:
                        strategy_stats[s]["fn"] += 1

        engine_a_stats = FalseStats(
            false_positives=a_fp, false_negatives=a_fn,
            true_positives=a_tp, true_negatives=a_tn,
            cases_with_ground_truth=cases_with_ground_truth,
        )
        engine_b_stats = FalseStats(
            false_positives=b_fp, false_negatives=b_fn,
            true_positives=b_tp, true_negatives=b_tn,
            cases_with_ground_truth=cases_with_ground_truth,
        )
        strategy_breakdown = {
            s: StrategyFalseStats(
                strategy=s,
                false_positives=v["fp"],
                false_negatives=v["fn"],
                true_positives=v["tp"],
                true_negatives=v["tn"],
                engine_a_only_positives=v["a_only_pos"],
                engine_b_only_positives=v["b_only_pos"],
            )
            for s, v in strategy_stats.items()
        }
        combined_fp, combined_fn, combined_tp, combined_tn = 0, 0, 0, 0
        for p in pairs:
            if p.expected_hallucination is None:
                continue
            a_halluc = p.engine_a_result and p.engine_a_result.is_hallucination
            b_halluc = p.engine_b_result and p.engine_b_result.is_hallucination
            combined_pos = a_halluc or b_halluc
            if not p.expected_hallucination:
                if combined_pos:
                    combined_fp += 1
                else:
                    combined_tn += 1
            else:
                if combined_pos:
                    combined_tp += 1
                else:
                    combined_fn += 1

        report.false_stats = FalseStats(
            false_positives=combined_fp,
            false_negatives=combined_fn,
            true_positives=combined_tp,
            true_negatives=combined_tn,
            cases_with_ground_truth=cases_with_ground_truth,
            engine_a_stats=engine_a_stats,
            engine_b_stats=engine_b_stats,
            strategy_breakdown=strategy_breakdown,
        )

        from .statistics import wilson_confidence_interval
        total_positive = combined_tp + combined_fp
        total_actual_positive = combined_tp + combined_fn
        if total_positive > 0:
            p_ci = wilson_confidence_interval(combined_tp, total_positive)
            report.false_stats.precision_ci_lower, report.false_stats.precision_ci_upper = p_ci
        if total_actual_positive > 0:
            r_ci = wilson_confidence_interval(combined_tp, total_actual_positive)
            report.false_stats.recall_ci_lower, report.false_stats.recall_ci_upper = r_ci

    def _compute_latency_stats(self, report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
        latencies_a = [
            p.engine_a_result.latency_ms
            for p in pairs
            if p.engine_a_result and p.engine_a_result.error is None
        ]
        latencies_b = [
            p.engine_b_result.latency_ms
            for p in pairs
            if p.engine_b_result and p.engine_b_result.error is None
        ]

        if latencies_a:
            report.latency_a_p50_ms = _percentile(latencies_a, 50)
            report.latency_a_p99_ms = _percentile(latencies_a, 99)
        if latencies_b:
            report.latency_b_p50_ms = _percentile(latencies_b, 50)
            report.latency_b_p99_ms = _percentile(latencies_b, 99)

        avg_p99 = (report.latency_a_p99_ms + report.latency_b_p99_ms) / 2
        if avg_p99 > 0:
            report.latency_diff_pct = abs(
                report.latency_b_p99_ms - report.latency_a_p99_ms
            ) / avg_p99 * 100

        if report.latency_a_p50_ms > 0 and report.latency_b_p50_ms > 0:
            avg_p50 = (report.latency_a_p50_ms + report.latency_b_p50_ms) / 2
            report.latency_diff_p50_pct = abs(
                report.latency_b_p50_ms - report.latency_a_p50_ms
            ) / avg_p50 * 100

        report.latency_sample_count_a = len(latencies_a)
        report.latency_sample_count_b = len(latencies_b)
        min_samples = self.config.consistency_thresholds.latency_p99_min_samples
        report.latency_p99_reliable = len(latencies_a) >= min_samples and len(latencies_b) >= min_samples

        if report.latency_a_p99_ms > 0 and report.latency_b_p99_ms > 0:
            if report.latency_a_p99_ms < report.latency_b_p99_ms:
                report.latency_faster_engine = self._engine_a_name
            elif report.latency_b_p99_ms < report.latency_a_p99_ms:
                report.latency_faster_engine = self._engine_b_name

    def _compute_anomaly_cases(self, report: ConsistencyReport, pairs: list[ConsistencyPair]) -> None:
        for p in pairs:
            if p.is_boundary_crossing:
                report.boundary_cases.append({
                    "case_id": p.case_id,
                    "organ": p.organ,
                    "expected": p.expected_grading,
                    "engine_a_grading": p.engine_a_result.grading if p.engine_a_result else None,
                    "engine_b_grading": p.engine_b_result.grading if p.engine_b_result else None,
                    "boundary_weight": p.boundary_weight,
                    "is_critical": p.is_critical_boundary,
                    "hallucination_issues_a": p.engine_a_result.hallucination_issues if p.engine_a_result else [],
                    "hallucination_issues_b": p.engine_b_result.hallucination_issues if p.engine_b_result else [],
                })
            if not p.grading_semantic_consistent:
                report.inconsistent_cases.append({
                    "case_id": p.case_id,
                    "organ": p.organ,
                    "expected": p.expected_grading,
                    "engine_a_grading": p.engine_a_result.grading if p.engine_a_result else None,
                    "engine_b_grading": p.engine_b_result.grading if p.engine_b_result else None,
                    "hallucination_issues_a": p.engine_a_result.hallucination_issues if p.engine_a_result else [],
                    "hallucination_issues_b": p.engine_b_result.hallucination_issues if p.engine_b_result else [],
                })

    async def cross_validate(
        self,
        cases: list[dict[str, Any]],
        n_folds: int = 5,
        seed: int | None = 42,
    ) -> list[CrossValidationResult]:
        """K-Fold交叉验证用于稳定性评估。

        此方法实现K-Fold交叉验证用于**稳定性评估**，而非传统ML的模型选择。
        通过对比不同数据子集下一致率和幻觉率的波动幅度，评估验证结果的统计稳定性。
        若数据量变化导致一致率大幅波动，说明验证结果不可靠。

        Args:
            cases: 测试用例列表
            n_folds: 折数，默认5
            seed: 随机种子，默认42（保证可复现性）

        Returns:
            每折的CrossValidationResult列表，含训练集/验证集一致率和幻觉率
        """
        import random

        if n_folds > len(cases):
            raise ValueError(
                f"n_folds ({n_folds}) cannot exceed number of cases ({len(cases)})"
            )

        results: list[CrossValidationResult] = []
        shuffled_cases = cases.copy()
        rng = random.Random(seed)
        rng.shuffle(shuffled_cases)

        fold_size = len(shuffled_cases) // n_folds

        for fold in range(n_folds):
            start_idx = fold * fold_size
            end_idx = start_idx + fold_size if fold < n_folds - 1 else len(shuffled_cases)

            validation_fold = shuffled_cases[start_idx:end_idx]
            train_subset = shuffled_cases[:start_idx] + shuffled_cases[end_idx:]

            logger.info(
                "cross_validation_fold_started",
                fold=fold + 1,
                n_folds=n_folds,
                train_count=len(train_subset),
                validation_count=len(validation_fold),
            )

            train_report = await self.verify(train_subset)
            validation_report = await self.verify(validation_fold)

            cv_result = CrossValidationResult(
                fold=fold + 1,
                train_count=len(train_subset),
                validation_count=len(validation_fold),
                train_accuracy=train_report.grading_consistency_rate,
                validation_accuracy=validation_report.grading_consistency_rate,
                train_hallucination_rate=(
                    (train_report.hallucination_a_count + train_report.hallucination_b_count)
                    / (2 * train_report.total_cases)
                    if train_report.total_cases > 0 else 0.0
                ),
                validation_hallucination_rate=(
                    (validation_report.hallucination_a_count + validation_report.hallucination_b_count)
                    / (2 * validation_report.total_cases)
                    if validation_report.total_cases > 0 else 0.0
                ),
                accuracy_diff=abs(
                    train_report.grading_consistency_rate - validation_report.grading_consistency_rate
                ),
                hallucination_rate_diff=abs(
                    (
                        (train_report.hallucination_a_count + train_report.hallucination_b_count)
                        / (2 * train_report.total_cases) if train_report.total_cases > 0 else 0.0
                    ) - (
                        (validation_report.hallucination_a_count + validation_report.hallucination_b_count)
                        / (2 * validation_report.total_cases) if validation_report.total_cases > 0 else 0.0
                    )
                ),
                train_boundary_diff_rate=train_report.clinical_boundary_diff_rate,
                validation_boundary_diff_rate=validation_report.clinical_boundary_diff_rate,
                boundary_diff_rate_diff=abs(train_report.clinical_boundary_diff_rate - validation_report.clinical_boundary_diff_rate),
            )
            results.append(cv_result)

            logger.info(
                "cross_validation_fold_completed",
                fold=fold + 1,
                train_accuracy=f"{cv_result.train_accuracy:.2%}",
                validation_accuracy=f"{cv_result.validation_accuracy:.2%}",
                accuracy_diff=f"{cv_result.accuracy_diff:.2%}",
            )

        logger.info(
            "cross_validation_completed",
            n_folds=n_folds,
            avg_accuracy_diff=sum(r.accuracy_diff for r in results) / len(results),
            max_accuracy_diff=max(r.accuracy_diff for r in results),
        )

        return results

    def report_to_dict(self, report: ConsistencyReport) -> dict[str, Any]:
        return report_to_dict(report)

    def report_to_json(self, report: ConsistencyReport, indent: int = 2) -> str:
        return report_to_json(report, indent)

    def _submit_to_drift_detector(self, report: ConsistencyReport) -> None:
        try:
            from app.inference.drift_detector import DriftDetector, MetricType
            detector = DriftDetector(
                model_name=f"{self._engine_a_name}_vs_{self._engine_b_name}",
            )
            metrics = [
                (MetricType.GRADING_CONSISTENCY, report.grading_consistency_rate),
                (MetricType.HALLUCINATION_RATE, report.hallucination_rate_a),
                (MetricType.LATENCY_P50, report.latency_a_p50_ms),
                (MetricType.LATENCY_P99, report.latency_a_p99_ms),
            ]
            for metric_type, value in metrics:
                alert = detector.detect_drift(metric_type, value)
                if alert is not None:
                    logger.warning(
                        "drift_detected",
                        metric=metric_type.value,
                        severity=alert.severity.value,
                        current=value,
                        baseline_mean=alert.baseline_mean,
                        deviation_pct=f"{alert.deviation_pct:.1f}%",
                    )
            for organ_name, organ_data in report.organ_breakdown.items():
                organ_gc = organ_data.get("grading_consistency_rate")
                if organ_gc is not None:
                    alert = detector.detect_drift(MetricType.GRADING_CONSISTENCY, organ_gc, organ=organ_name)
                    if alert is not None:
                        logger.warning(
                            "drift_detected",
                            metric="grading_consistency",
                            organ=organ_name,
                            severity=alert.severity.value,
                            current=organ_gc,
                            baseline_mean=alert.baseline_mean,
                        )
        except Exception as e:
            logger.debug("drift_detector_submit_skipped", error=str(e))
