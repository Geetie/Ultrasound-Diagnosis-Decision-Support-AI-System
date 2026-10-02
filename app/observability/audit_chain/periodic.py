import asyncio

import structlog

from app.config.settings import settings

from .cross_chain import AuditCrossChain
from .verifier import AuditChainVerifier

_logger = structlog.get_logger("app.observability.audit_chain.periodic")
_periodic_task: asyncio.Task | None = None
_periodic_running = False


async def start_audit_chain_periodic_tasks(
    db_session_factory,
    interval_hours: int | None = None,
) -> None:
    global _periodic_task, _periodic_running
    if _periodic_running:
        return
    _periodic_running = True
    hours = interval_hours or settings.obs_audit_cross_chain_interval_hours

    async def _run():
        while _periodic_running:
            await asyncio.sleep(hours * 3600)
            try:
                async for db in db_session_factory():
                    cross_chain = AuditCrossChain(db)
                    verifier = AuditChainVerifier(db)
                    results = await verifier.verify_all()
                    if not results["all_valid"]:
                        _logger.critical(
                            "audit_chain_integrity_violation",
                            broken_sessions=results["session_chains"]["broken"],
                            broken_critical=results["critical_chains"]["broken"],
                        )
                    else:
                        _logger.info(
                            "audit_chain_integrity_verified",
                            total_session_chains=results["session_chains"]["total"],
                            total_critical_chains=results["critical_chains"]["total"],
                        )
                    break
            except Exception as e:
                _logger.error("periodic_audit_verification_failed", error=str(e))

    _periodic_task = asyncio.create_task(_run())
    _logger.info("cross_chain_periodic_task_started", interval_hours=hours)


async def stop_audit_chain_periodic_tasks() -> None:
    global _periodic_running, _periodic_task
    _periodic_running = False
    if _periodic_task:
        _periodic_task.cancel()
        try:
            await _periodic_task
        except asyncio.CancelledError:
            pass
        _periodic_task = None
    _logger.info("cross_chain_periodic_task_stopped")
