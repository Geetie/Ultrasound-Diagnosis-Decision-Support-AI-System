from typing import Any

import structlog

from safety_gateway.validation_result import CheckResult, DegradedReason
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.checker.phase_base import CheckPhase, CheckContext

MAX_RECURSION_DEPTH = 20

logger = structlog.get_logger()


class DesensitizationPhase(CheckPhase):

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
        desensitization_degraded = context.desensitization_degraded

        if conclusion_suppressed:
            observations_only = {k: v for k, v in output_data.items() if k != "diagnostic_conclusion"}
            sensitive_data = await self.check_desensitization(observations_only)
        else:
            sensitive_data = await self.check_desensitization(output_data)

        if not sensitive_data:
            return None

        if not fusion_available:
            logger.error("sg_fusion_unavailable_sensitive_detected_e4005a", patient_id=patient_id)
            return CheckResult.failed(
                consent_status=context.consent_status,
                message="融合层不可用+安全网关检测到敏感数据，拒绝所有诊断输出（E4005A1）",
                elapsed_ms=elapsed_ms,
                sensitive_data_detected=sensitive_data,
                degraded_reason=DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
                consent_check_timeout=context.is_timeout,
            )

        if desensitization_degraded:
            logger.error(
                "sg_fusion_desensitization_degraded_sensitive_data_rejected",
                patient_id=patient_id,
                types=sensitive_data,
            )
            return CheckResult.failed(
                consent_status=context.consent_status,
                message="融合层脱敏已降级+安全网关检测到敏感数据，拒绝输出",
                elapsed_ms=elapsed_ms,
                sensitive_data_detected=sensitive_data,
                degraded_reason=DegradedReason.DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED,
                consent_check_timeout=context.is_timeout,
            )

        logger.warning(
            "sg_sensitive_data_detected_entering_degraded_mode",
            patient_id=patient_id,
            types=sensitive_data,
        )
        return CheckResult.degraded(
            consent_status=context.consent_status,
            message="检测到敏感数据，进入降级输出模式",
            degraded_reason=DegradedReason.SENSITIVE_DATA_DETECTED,
            elapsed_ms=elapsed_ms,
            sensitive_data_detected=sensitive_data,
            consent_check_timeout=context.is_timeout,
        )

    async def check_desensitization(self, data: dict[str, Any]) -> list[str]:
        detected_types = []
        for value in data.values():
            detected = await self._check_value_recursive(value, depth=0)
            detected_types.extend(detected)
        return list(set(detected_types))

    async def _check_value_recursive(self, value: Any, depth: int) -> list[str]:
        if depth >= MAX_RECURSION_DEPTH:
            logger.warning("sg_recursion_depth_exceeded", depth=depth)
            return []
        detected_types = []
        if isinstance(value, str):
            detected = sg_desensitization_engine.check_sensitive_data(value)
            detected_types.extend(detected)
        elif isinstance(value, (int, float)):
            detected = sg_desensitization_engine.check_sensitive_data(str(value))
            detected_types.extend(detected)
        elif isinstance(value, dict):
            for v in value.values():
                detected = await self._check_value_recursive(v, depth + 1)
                detected_types.extend(detected)
        elif isinstance(value, list):
            for item in value:
                detected = await self._check_value_recursive(item, depth + 1)
                detected_types.extend(detected)
        return detected_types
