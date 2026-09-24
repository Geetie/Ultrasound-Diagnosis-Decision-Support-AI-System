import structlog

from safety_gateway.checker.phase_base import CheckContext, CheckPhase
from safety_gateway.validation_result import CheckResult, DegradedReason

logger = structlog.get_logger()


class EdgeConnectivityPhase(CheckPhase):

    async def check(self, context: CheckContext) -> CheckResult | None:
        raise NotImplementedError("Use check_with_params instead")

    def check_with_params(
        self,
        patient_id: str,
        edge_connected: bool,
        context: CheckContext,
        elapsed_ms: float,
    ) -> CheckResult | None:
        if not edge_connected:
            logger.warning("sg_edge_disconnected_entering_degraded_mode", patient_id=patient_id)
            return CheckResult.degraded(
                consent_status=context.consent_status,
                message="边缘后端断连，进入本地降级模式",
                degraded_reason=DegradedReason.EDGE_DISCONNECTED,
                elapsed_ms=elapsed_ms,
                consent_check_timeout=context.is_timeout,
            )
        return None
