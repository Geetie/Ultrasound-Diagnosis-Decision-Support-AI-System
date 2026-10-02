from typing import Any

import structlog

from safety_gateway.checker.phase_base import CheckContext, CheckPhase
from safety_gateway.contraindication import get_contraindication_engine
from safety_gateway.validation_result import CheckResult, DegradedReason

logger = structlog.get_logger()


class ContraindicationThresholdPhase(CheckPhase):
    """禁忌校验阶段

    仅负责禁忌条件检测。阈值校验由后续ThresholdPhase独立执行。
    本阶段检测到禁忌时，与context中已有的阈值结果组合判定是否为组合故障。
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
        fusion_available = context.fusion_available
        organ = output_data.get("organ", "unknown")
        diagnosis_data = output_data.get("diagnosis_data", {})

        contraindication_result = await self.check_contraindication(patient_id, organ, diagnosis_data)
        has_contraindication = contraindication_result["has_contraindication"]

        if not has_contraindication:
            if not fusion_available:
                logger.warning("sg_fusion_unavailable_degraded_output", patient_id=patient_id)
                return CheckResult.degraded(
                    consent_status=context.consent_status,
                    message="融合层不可用，SafetyGateway降级输出仅观察描述",
                    degraded_reason=DegradedReason.FUSION_UNAVAILABLE,
                    elapsed_ms=elapsed_ms,
                    consent_check_timeout=context.is_timeout,
                )
            return None

        has_threshold_failure = any(
            not r.get("passed", True) for r in context.threshold_results
        )
        combined_with_fusion_unavailable = not fusion_available
        if has_threshold_failure or combined_with_fusion_unavailable:
            logger.error(
                "sg_combined_failure",
                patient_id=patient_id,
                has_contraindication=has_contraindication,
                has_threshold_failure=has_threshold_failure,
                fusion_available=fusion_available,
            )
            context.contraindication_matches = contraindication_result["contraindications"]
            return CheckResult.degraded(
                consent_status=context.consent_status,
                message="多项故障同时发生，进入组合降级模式",
                degraded_reason=DegradedReason.COMBINED_FAILURE,
                elapsed_ms=elapsed_ms,
                contraindication_matches=context.contraindication_matches,
                threshold_results=context.threshold_results,
                consent_check_timeout=context.is_timeout,
            )

        logger.warning(
            "sg_contraindication_detected",
            patient_id=patient_id,
            organ=organ,
            contraindications=contraindication_result["contraindications"],
        )
        context.contraindication_matches = contraindication_result["contraindications"]
        return CheckResult.degraded(
            consent_status=context.consent_status,
            message=f"检测到禁忌条件：{contraindication_result['message']}",
            degraded_reason=DegradedReason.CONTRAINDICATION_HIT,
            elapsed_ms=elapsed_ms,
            contraindication_matches=context.contraindication_matches,
            consent_check_timeout=context.is_timeout,
        )

    async def check_contraindication(
        self,
        patient_id: str,
        organ: str,
        diagnosis_data: dict[str, Any],
    ) -> dict[str, Any]:
        engine = get_contraindication_engine()
        matches = engine.check(organ, diagnosis_data)
        match_dicts = [
            {
                "rule_id": m.rule_id,
                "organ": m.organ,
                "condition": m.condition,
                "description": m.description,
                "severity": m.severity,
            }
            for m in matches
        ]
        return {
            "has_contraindication": len(matches) > 0,
            "contraindications": match_dicts,
            "message": f"检测到{len(matches)}条禁忌" if matches else "无禁忌",
        }
