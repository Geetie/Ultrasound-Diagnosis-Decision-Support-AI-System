from typing import Any, ClassVar
import time

import structlog

from safety_gateway.sg_config import sg_settings as settings
from safety_gateway.validation_result import ConsentStatus
from safety_gateway.validation_result import CheckResult, CheckResultStatus, DegradedReason
from safety_gateway.consent_checker import ConsentCheckResult
from safety_gateway.health import HealthChecker
from safety_gateway.degraded_output import (
    apply_degraded_output as _apply_degraded_output,
    filter_forbidden_terms as _filter_forbidden_terms,
    FORBIDDEN_GRADING_TERMS,
)
from safety_gateway.checker.phase_base import CheckContext
from safety_gateway.checker.consent_phase import ConsentPhase
from safety_gateway.checker.edge_connectivity_phase import EdgeConnectivityPhase
from safety_gateway.checker.desensitization_phase import DesensitizationPhase
from safety_gateway.checker.contraindication_threshold_phase import ContraindicationThresholdPhase
from safety_gateway.checker.threshold_phase import ThresholdPhase
from safety_gateway.checker.consistency_phase import ConsistencyPhase

logger = structlog.get_logger()


class ElapsedTimer:
    __slots__ = ("_start",)

    def __init__(self):
        self._start = time.monotonic()

    def elapsed_ms(self) -> float:
        return round((time.monotonic() - self._start) * 1000, 2)


class SafetyGatewayChecker:
    """安全网关主校验器（编排器）

    协调6个校验阶段，按顺序执行，任一阶段返回非None即短路。
    阶段：同意校验 → 边缘连接 → 脱敏校验 → 阈值 → 禁忌(含组合判定) → 一致性

    阈值先于禁忌执行：ThresholdPhase将阈值结果写入context.threshold_results，
    ContraindicationThresholdPhase据此判定禁忌+阈值组合故障。
    """

    DEFAULT_WHITELIST_FIELDS: ClassVar[list[str]] = [
        "organ_name",
        "anatomical_location",
        "echogenicity",
        "boundary",
        "morphology",
        "vascularity",
        "calcification",
        "lesion_size_measurements",
        "observations",
    ]

    FORBIDDEN_GRADING_TERMS: ClassVar[tuple[str, ...]] = FORBIDDEN_GRADING_TERMS

    def __init__(self):
        self._whitelist_fields = (
            settings.safety_gateway_whitelist_fields
            if settings.safety_gateway_whitelist_fields
            else self.DEFAULT_WHITELIST_FIELDS
        )

        self._consent_phase = ConsentPhase()
        self._edge_connectivity_phase = EdgeConnectivityPhase()
        self._desensitization_phase = DesensitizationPhase()
        self._contraindication_threshold_phase = ContraindicationThresholdPhase()
        self._threshold_phase = ThresholdPhase()
        self._consistency_phase = ConsistencyPhase()

        logger.info(
            "safety_gateway_checker_initialized",
            whitelist_fields=self._whitelist_fields,
        )

    async def perform_full_check(
        self,
        patient_id: str,
        output_data: dict[str, Any],
        edge_connected: bool = True,
        consent_status_at_input: str | None = None,
        desensitization_degraded: bool = False,
        conclusion_suppressed: bool = False,
        hallucination_severity: str | None = None,
    ) -> CheckResult:
        timer = ElapsedTimer()
        health_checker = _get_health_checker()

        sg_fusion_available = await health_checker.get_fusion_availability()
        effective_fusion_available = sg_fusion_available if sg_fusion_available is not None else False

        if not output_data or all(
            v is None or v == "" or v == [] or v == {}
            for v in output_data.values()
            if not isinstance(v, (int, float, bool))
        ):
            logger.warning("sg_empty_output_data_rejected", patient_id=patient_id)
            return CheckResult.failed(
                consent_status=None,
                message="输出数据为空，拒绝输出诊断结论",
                elapsed_ms=timer.elapsed_ms(),
            )

        if consent_status_at_input is not None and consent_status_at_input not in {"granted", "denied", "withdrawn", "pending"}:
            logger.warning(
                "sg_consent_status_at_input_invalid",
                patient_id=patient_id,
                consent_status_at_input=consent_status_at_input,
            )
            return CheckResult.failed(
                consent_status=None,
                message=f"入口同意状态值非法: {consent_status_at_input}，拒绝输出诊断结论",
                elapsed_ms=timer.elapsed_ms(),
            )

        ctx = CheckContext()
        ctx.fusion_available = effective_fusion_available
        ctx.desensitization_degraded = desensitization_degraded
        try:
            result = await self._consent_phase.check_with_params(
                patient_id, effective_fusion_available, ctx, timer.elapsed_ms(),
                consent_status_at_input=consent_status_at_input,
            )
            if result is not None:
                return result

            result = self._edge_connectivity_phase.check_with_params(
                patient_id, edge_connected, ctx, timer.elapsed_ms(),
            )
            if result is not None:
                return result

            result = await self._desensitization_phase.check_with_params(
                patient_id, output_data, ctx, timer.elapsed_ms(),
                conclusion_suppressed=conclusion_suppressed,
            )
            if result is not None:
                return result

            result = await self._threshold_phase.check_with_params(
                patient_id, output_data, ctx, timer.elapsed_ms(),
                conclusion_suppressed=conclusion_suppressed,
            )
            if result is not None:
                return result

            result = await self._contraindication_threshold_phase.check_with_params(
                patient_id, output_data, ctx, timer.elapsed_ms(),
                conclusion_suppressed=conclusion_suppressed,
            )
            if result is not None:
                return result

            result = await self._consistency_phase.check_with_params(
                patient_id, output_data, ctx, timer.elapsed_ms(),
                conclusion_suppressed=conclusion_suppressed,
            )
            if result is not None:
                return result

            check_result = CheckResult.passed(
                consent_status=ctx.consent_status,
                message="安全校验通过（五项校验全部通过）",
                elapsed_ms=timer.elapsed_ms(),
                threshold_results=ctx.threshold_results,
                consent_check_timeout=ctx.is_timeout,
                consent_status_source="pg_direct",
            )

            if hallucination_severity == "severe":
                check_result.hallucination_warning = True
                check_result.message = "安全校验通过，但检测到严重幻觉风险，请人工核验"

            return check_result
        finally:
            total_elapsed = timer.elapsed_ms()
            await health_checker.record_latency(total_elapsed)
            await health_checker.record_e2e_latency(total_elapsed)

    async def check_desensitization(self, data: dict[str, Any]) -> list[str]:
        return await self._desensitization_phase.check_desensitization(data)

    async def check_contraindication(
        self,
        patient_id: str,
        organ: str,
        diagnosis_data: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._contraindication_threshold_phase.check_contraindication(patient_id, organ, diagnosis_data)

    async def check_consistency(
        self,
        fusion_output: dict[str, Any],
        specialist_conclusions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._consistency_phase.check_consistency(fusion_output, specialist_conclusions)

    def _filter_forbidden_terms(self, value: Any, key: str = "", depth: int = 0) -> Any:
        return _filter_forbidden_terms(value, key=key, depth=depth)

    def apply_degraded_output(
        self,
        data: dict[str, Any],
        reason: DegradedReason = DegradedReason.SENSITIVE_DATA_DETECTED,
        fusion_available: bool = True,
    ) -> dict[str, Any]:
        return _apply_degraded_output(data, reason=reason, whitelist_fields=self._whitelist_fields, fusion_available=fusion_available)

    async def check_consent_status(self, patient_id: str) -> ConsentCheckResult:
        from safety_gateway.consent_checker import get_consent_checker
        return await get_consent_checker().check_consent_status(patient_id)

    def get_whitelist_fields(self) -> list[str]:
        return self._whitelist_fields or self.DEFAULT_WHITELIST_FIELDS

    @property
    def _consent_checker(self):
        from safety_gateway.consent_checker import get_consent_checker
        return get_consent_checker()

    @property
    def _contraindication_engine(self):
        from safety_gateway.contraindication import get_contraindication_engine
        return get_contraindication_engine()

    @property
    def _threshold_engine(self):
        from safety_gateway.threshold import get_threshold_engine
        return get_threshold_engine()

    async def close(self) -> None:
        from safety_gateway.consent_checker import get_consent_checker
        await get_consent_checker().close()
        logger.info("safety_gateway_checker_closed")


from safety_gateway.registry import sg_registry
from threading import Lock

_sg_checker_lock = Lock()
_sg_health_checker_lock = Lock()


def _get_health_checker() -> HealthChecker:
    if not sg_registry.is_registered("health_checker"):
        with _sg_health_checker_lock:
            if not sg_registry.is_registered("health_checker"):
                sg_registry.register("health_checker", HealthChecker)
    return sg_registry.get("health_checker")


def get_safety_gateway_checker() -> SafetyGatewayChecker:
    if not sg_registry.is_registered("checker"):
        with _sg_checker_lock:
            if not sg_registry.is_registered("checker"):
                sg_registry.register("checker", SafetyGatewayChecker)
    return sg_registry.get("checker")


def _reset_safety_gateway_checker() -> None:
    sg_registry.reset("checker")
    sg_registry.reset("health_checker")
