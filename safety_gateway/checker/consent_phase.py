import structlog

from safety_gateway.validation_result import ConsentStatus, CheckResult, DegradedReason
from safety_gateway.errors import DatabaseUnavailableError, CombinedUnavailableError
from safety_gateway.consent_checker import get_consent_checker
from safety_gateway.checker.phase_base import CheckPhase, CheckContext
from safety_gateway.circuit_breaker import circuit_breaker_manager

logger = structlog.get_logger()


class ConsentPhase(CheckPhase):

    async def check(self, context: CheckContext) -> CheckResult | None:
        raise NotImplementedError("Use check_with_params instead")

    _FAST_REJECT_STATUSES = frozenset({"denied", "withdrawn", "pending"})

    async def check_with_params(
        self,
        patient_id: str,
        fusion_available: bool,
        context: CheckContext,
        elapsed_ms: float,
        consent_status_at_input: str | None = None,
    ) -> CheckResult | None:
        if consent_status_at_input is not None and consent_status_at_input in self._FAST_REJECT_STATUSES:
            logger.info(
                "sg_consent_input_fast_reject",
                patient_id=patient_id,
                consent_status_at_input=consent_status_at_input,
            )
            context.consent_status = ConsentStatus(consent_status_at_input)
            context.is_timeout = False
            context.is_db_error = False
            return CheckResult.failed(
                consent_status=ConsentStatus(consent_status_at_input),
                message=f"入口快速拒绝：患者同意状态为{consent_status_at_input}，拒绝输出诊断结论",
                elapsed_ms=elapsed_ms,
                consent_status_source="input_fast_reject",
            )

        consent_checker = get_consent_checker()
        consent_result = await consent_checker.check_consent_status(patient_id)
        context.consent_status = consent_result.status
        context.is_timeout = consent_result.timeout
        context.is_db_error = consent_result.error

        if context.is_db_error:
            if consent_status_at_input == "withdrawn":
                logger.error("sg_unavailable_consent_withdrawn_db_error", patient_id=patient_id)
                return CheckResult.failed(
                    consent_status=ConsentStatus.WITHDRAWN,
                    message="安全网关不可用且患者已撤回知情同意，拒绝所有诊断输出（E4005A2）",
                    elapsed_ms=elapsed_ms,
                    degraded_mode=True,
                    degraded_reason=DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN,
                    consent_status_source="input_fast_reject_db_error",
                )
            logger.error("sg_consent_check_db_error_returning_unavailable", patient_id=patient_id)
            breaker = circuit_breaker_manager.get_circuit_breaker("sg_db_consent")
            await breaker.record_failure()
            if not fusion_available:
                raise CombinedUnavailableError(
                    error_message="数据库不可用且融合层不可用，SafetyGateway+融合层同时不可用（E4005C）",
                    detail="sg_db_and_fusion_unavailable",
                )
            raise DatabaseUnavailableError(
                error_message="数据库查询异常，无法获取同意状态（E4005B）——一期策略：PG不可用时直接拒绝，不降级为Redis缓存（对齐01-系统架构设计§3.4）",
                detail="sg_db_unavailable",
            )

        if context.consent_status == ConsentStatus.WITHDRAWN:
            return CheckResult.failed(
                consent_status=context.consent_status,
                message="患者已撤回知情同意，拒绝输出诊断结论",
                elapsed_ms=elapsed_ms,
                consent_check_timeout=context.is_timeout,
                consent_status_source="pg_direct",
            )

        if context.is_timeout:
            logger.warning("sg_consent_check_timeout_rejecting_output", patient_id=patient_id)
            return CheckResult.failed(
                consent_status=context.consent_status,
                message="同意状态查询超时，拒绝输出诊断结论（E4005B）——PG不可用时直接拒绝",
                elapsed_ms=elapsed_ms,
                degraded_mode=True,
                degraded_reason=DegradedReason.DB_TIMEOUT,
                consent_check_timeout=True,
                consent_status_source="pg_direct",
            )

        if context.consent_status is None:
            logger.error("sg_consent_status_unknown_rejecting_output", patient_id=patient_id)
            return CheckResult.failed(
                consent_status=None,
                message="同意状态未知，拒绝输出诊断结论（权威防线：未知=不安全）",
                elapsed_ms=elapsed_ms,
                degraded_mode=True,
                degraded_reason=DegradedReason.CONSENT_STATUS_UNKNOWN,
                consent_check_timeout=context.is_timeout,
                consent_status_source="pg_direct",
            )

        if context.consent_status == ConsentStatus.PENDING:
            return CheckResult.failed(
                consent_status=context.consent_status,
                message="患者同意状态为待定（未签署知情同意），拒绝输出诊断结论",
                elapsed_ms=elapsed_ms,
                consent_check_timeout=context.is_timeout,
                consent_status_source="pg_direct",
            )

        if context.consent_status == ConsentStatus.DENIED:
            return CheckResult.failed(
                consent_status=context.consent_status,
                message="患者未签署知情同意，拒绝输出诊断结论",
                elapsed_ms=elapsed_ms,
                consent_check_timeout=context.is_timeout,
                consent_status_source="pg_direct",
            )

        return None
