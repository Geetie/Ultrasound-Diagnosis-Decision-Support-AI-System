import structlog
from typing import Any

from safety_gateway.validation_result import CheckResult, CheckResultStatus, DegradedReason
from safety_gateway.checker.phase_base import CheckPhase, CheckContext
from safety_gateway.threshold import get_threshold_engine

logger = structlog.get_logger()


class ThresholdPhase(CheckPhase):
    """置信度阈值校验阶段

    从ContraindicationThresholdPhase中拆分出来，
    实现设计文档5阶段校验：同意→边缘→脱敏→禁忌→阈值→一致性
    """

    async def check(self, context: CheckContext) -> CheckResult | None:
        raise NotImplementedError("Use check_with_params instead")

    async def check_with_params(
        self,
        patient_id: str,
        output_data: dict[str, Any],
        context: CheckContext,
        elapsed_ms: float,
        conclusion_suppressed: bool = False,
    ) -> CheckResult | None:
        conclusions = output_data.get("conclusions", output_data.get("diagnoses", []))
        if not conclusions:
            return None

        threshold_engine = get_threshold_engine()
        threshold_results = await threshold_engine.check_multiple(conclusions)
        context.threshold_results = [r.model_dump() for r in threshold_results]

        failed_results = [r for r in threshold_results if not r.passed]
        if not failed_results:
            return None

        logger.warning(
            "sg_confidence_below_threshold",
            patient_id=patient_id,
            failed_count=len(failed_results),
        )

        has_contraindication = bool(context.contraindication_matches)
        is_combined = has_contraindication or not context.fusion_available

        if is_combined:
            return CheckResult.degraded(
                consent_status=context.consent_status,
                message=f"置信度低于阈值且存在组合故障，进入降级模式",
                degraded_reason=DegradedReason.COMBINED_FAILURE,
                elapsed_ms=elapsed_ms,
                threshold_results=context.threshold_results,
                consent_check_timeout=context.is_timeout,
            )

        return CheckResult.degraded(
            consent_status=context.consent_status,
            message=f"置信度低于阈值：{', '.join(f'{r.organ}({r.confidence}<{r.threshold})' for r in failed_results)}",
            degraded_reason=DegradedReason.CONFIDENCE_BELOW_THRESHOLD,
            elapsed_ms=elapsed_ms,
            threshold_results=context.threshold_results,
            consent_check_timeout=context.is_timeout,
        )
