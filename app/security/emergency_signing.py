import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.config.settings import settings
from app.security.hsm import DegradedPhase, KeySlot, hsm_manager

logger = structlog.get_logger()

_EMERGENCY_SIGNING_KEY_NAME = "emergency_signing_key"


class EmergencySigningManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._daily_signature_count: int = 0
        self._daily_count_reset_at: datetime | None = None

    async def _atomic_check_and_increment_daily_count(self) -> int:
        from app.config.redis_keys import RedisKey
        from app.config.settings import settings
        max_daily = settings.hsm_emergency_signing_max_daily
        now = datetime.now(timezone.utc)
        date_key = now.strftime("%Y%m%d")
        redis_key = RedisKey.hsm_emergency_daily_count(date_key)

        try:
            from app.config.redis import RedisPool
            redis_client = await RedisPool.get_client()
            check_and_incr_script = """
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            if count > tonumber(ARGV[2]) then
                redis.call('DECR', KEYS[1])
                return count
            end
            return count
            """
            count = await redis_client.eval(check_and_incr_script, 1, redis_key, 86400 * 2, max_daily)
            current_count = int(count)
        except Exception as e:
            logger.warning("failed_to_atomic_check_and_increment_daily_count", error=str(e))
            with self._lock:
                if self._daily_count_reset_at is None or \
                   (now - self._daily_count_reset_at) > timedelta(days=1):
                    self._daily_signature_count = 0
                    self._daily_count_reset_at = now
                self._daily_signature_count += 1
                current_count = self._daily_signature_count

        return current_count

    async def enable_emergency_signing(
        self, operator_id: str, approver_id: str, reason: str,
        recovery_confirmed: bool = False,
    ) -> None:
        if not operator_id or not approver_id:
            raise RuntimeError("Both operator_id and approver_id are required for dual-person verification")
        if operator_id == approver_id:
            raise RuntimeError("operator_id and approver_id must be different individuals for dual-person verification")

        from app.config.settings import settings
        if not settings.hsm_emergency_signing_enabled:
            raise RuntimeError("Emergency signing is not enabled in configuration")

        phase = hsm_manager.get_degraded_phase()
        if phase not in (DegradedPhase.RESTRICTED, DegradedPhase.EMERGENCY):
            raise RuntimeError(f"Emergency signing can only be enabled during RESTRICTED or EMERGENCY phase, current: {phase.value}")

        if not hsm_manager.is_degraded():
            raise RuntimeError("HSM is not in degraded state")

        degraded_duration = datetime.now(timezone.utc) - hsm_manager.get_degraded_at() if hsm_manager.get_degraded_at() else timedelta(0)
        if degraded_duration < timedelta(minutes=5):
            raise RuntimeError("Emergency signing can only be enabled after HSM degraded for 5 minutes")

        key_info = hsm_manager.get_key_info(_EMERGENCY_SIGNING_KEY_NAME)
        if not key_info:
            hsm_manager.generate_key_pair(_EMERGENCY_SIGNING_KEY_NAME, slot=KeySlot.EMERGENCY_SIGNING)
            try:
                from app.services.audit_service import AuditService
                audit_svc = AuditService()
                await audit_svc.log_action(
                    action="EMERGENCY_KEY_AUTO_GENERATED",
                    actor="system",
                    target_type="hsm",
                    target_id=_EMERGENCY_SIGNING_KEY_NAME,
                    detail={"source": "enable_emergency_signing", "slot": KeySlot.EMERGENCY_SIGNING.value},
                )
            except Exception as e:
                logger.warning("failed_to_audit_emergency_key_generation", error=str(e))

        if not recovery_confirmed:
            logger.warning("emergency_signing_enabled_without_recovery_confirmation",
                           operator_id=operator_id, approver_id=approver_id)

        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()
            ttl_seconds = 72 * 3600
            async with redis_client.pipeline(transaction=True) as pipe:
                await pipe.set(RedisKey.hsm_emergency_signing("enabled"), "true", ex=ttl_seconds)
                await pipe.set(RedisKey.hsm_emergency_signing("operator"), operator_id, ex=ttl_seconds)
                await pipe.set(RedisKey.hsm_emergency_signing("approver"), approver_id, ex=ttl_seconds)
                await pipe.set(RedisKey.hsm_emergency_signing("reason"), reason, ex=ttl_seconds)
                await pipe.set(RedisKey.hsm_emergency_signing("enabled_at"), datetime.now(timezone.utc).isoformat(), ex=ttl_seconds)
                await pipe.execute()
        except Exception as e:
            logger.error("failed_to_persist_emergency_signing_state", error=str(e))

        try:
            from app.services.audit_service import AuditService
            audit_svc = AuditService()
            await audit_svc.log_action(
                action="EMERGENCY_SIGNING_ENABLED",
                actor=operator_id,
                target_type="hsm",
                target_id="emergency_signing",
                detail={"approver": approver_id, "reason": reason, "degraded_phase": phase.value, "recovery_confirmed": recovery_confirmed},
            )
        except Exception as e:
            logger.warning("failed_to_audit_emergency_signing_enable", error=str(e))

        logger.critical("emergency_signing_enabled",
                       operator_id=operator_id, approver_id=approver_id, reason=reason, phase=phase.value)

    async def disable_emergency_signing(self, operator_id: str) -> None:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()

            verification_ttl = settings.hsm_emergency_signing_verification_hours * 3600
            try:
                await redis_client.expire(RedisKey.hsm_emergency_signing("enabled"), verification_ttl)
            except Exception as e:
                logger.error("failed_to_set_verification_window_ttl", error=str(e))

            async with redis_client.pipeline(transaction=True) as pipe:
                await pipe.delete(RedisKey.hsm_emergency_signing("operator"))
                await pipe.delete(RedisKey.hsm_emergency_signing("approver"))
                await pipe.delete(RedisKey.hsm_emergency_signing("reason"))
                await pipe.delete(RedisKey.hsm_emergency_signing("enabled_at"))
                await pipe.execute()
        except Exception as e:
            logger.error("failed_to_clear_emergency_signing_state", error=str(e))

        try:
            from app.services.audit_service import AuditService
            audit_svc = AuditService()
            await audit_svc.log_action(
                action="EMERGENCY_SIGNING_DISABLED",
                actor=operator_id,
                target_type="hsm",
                target_id="emergency_signing",
                detail={},
            )
        except Exception as e:
            logger.warning("failed_to_audit_emergency_signing_disable", error=str(e))

        logger.info("emergency_signing_disabled", operator_id=operator_id)

    async def is_emergency_signing_active(self) -> bool:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()
            value = await redis_client.get(RedisKey.hsm_emergency_signing("enabled"))
            return value == b"true"
        except Exception as exc:
            # A.60：Redis 故障时返回 False 是**fail-closed**（安全方向正确），但原先零留痕 ——
            # 现象是"紧急签名怎么都用不了"，而根因（基础设施故障）查不出来。必须记 warning。
            logger.warning(
                "emergency_signing_state_read_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    async def sign_emergency(self, key_name: str, data: bytes) -> tuple[bytes, dict[str, Any]]:
        if not await self.is_emergency_signing_active():
            raise RuntimeError("Emergency signing is not active")

        from app.config.settings import settings
        current_count = await self._atomic_check_and_increment_daily_count()
        if current_count > settings.hsm_emergency_signing_max_daily:
            raise RuntimeError("Daily emergency signature limit exceeded")

        emergency_key_info = hsm_manager.get_key_info(_EMERGENCY_SIGNING_KEY_NAME)
        if not emergency_key_info:
            hsm_manager.generate_key_pair(_EMERGENCY_SIGNING_KEY_NAME, slot=KeySlot.EMERGENCY_SIGNING)
            logger.critical("emergency_key_auto_generated",
                           source="sign_emergency", key_name=_EMERGENCY_SIGNING_KEY_NAME)

        signature = hsm_manager.sign(_EMERGENCY_SIGNING_KEY_NAME, data)

        metadata = {
            "emergency": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "daily_count": current_count,
        }

        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()
            operator = await redis_client.get(RedisKey.hsm_emergency_signing("operator"))
            metadata["operator_id"] = operator.decode() if operator else "unknown"
        except Exception as exc:
            # A.60：取不到操作人时仍落 "unknown"，但**必须留痕** —— 紧急签名的审计要求
            # 「谁在什么情况下签的」，静默降级会让审计记录失去可解释性。
            logger.warning(
                "emergency_signing_operator_read_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            metadata["operator_id"] = "unknown"

        logger.warning("emergency_signature_completed",
                       key_name=_EMERGENCY_SIGNING_KEY_NAME, daily_count=current_count)

        return signature, metadata

    async def sign_jwt_emergency(
        self, key_name: str, header: dict[str, Any], payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        if not await self.is_emergency_signing_active():
            raise RuntimeError("Emergency signing is not active")

        from app.config.settings import settings
        current_count = await self._atomic_check_and_increment_daily_count()
        if current_count > settings.hsm_emergency_signing_max_daily:
            raise RuntimeError("Daily emergency signature limit exceeded")

        emergency_key_info = hsm_manager.get_key_info(_EMERGENCY_SIGNING_KEY_NAME)
        if not emergency_key_info:
            hsm_manager.generate_key_pair(_EMERGENCY_SIGNING_KEY_NAME, slot=KeySlot.EMERGENCY_SIGNING)
            logger.critical("emergency_key_auto_generated",
                           source="sign_jwt_emergency", key_name=_EMERGENCY_SIGNING_KEY_NAME)

        jwt_token = hsm_manager.sign_jwt(_EMERGENCY_SIGNING_KEY_NAME, header, payload)

        metadata = {
            "emergency": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "daily_count": current_count,
        }

        logger.warning("emergency_jwt_signature_completed",
                       key_name=_EMERGENCY_SIGNING_KEY_NAME, daily_count=current_count)

        return jwt_token, metadata

    async def get_pending_verification_reports(self) -> list[dict[str, Any]]:
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()
            reports = []
            async for key in redis_client.scan_iter(f"{RedisKey.hsm_emergency_pending_verification('')}*"):
                data = await redis_client.get(key)
                if data:
                    import json
                    reports.append(json.loads(data))
            return reports
        except Exception as e:
            logger.error("failed_to_get_pending_verification_reports", error=str(e))
            return []

    async def verify_emergency_signature(
        self, original_data: bytes, signature_data: bytes, report_id: str
    ) -> bool:
        try:
            verified = hsm_manager.verify_signature(_EMERGENCY_SIGNING_KEY_NAME, original_data, signature_data)
            if not verified:
                logger.critical("emergency_signature_verification_failed",
                               report_id=report_id, reason="signature_invalid")
                try:
                    from app.services.audit_service import AuditService
                    audit_svc = AuditService()
                    await audit_svc.log_action(
                        action="EMERGENCY_SIGNATURE_VERIFICATION_FAILED",
                        actor="system",
                        target_type="report",
                        target_id=report_id,
                        detail={"verification_result": "invalid_signature"},
                    )
                except Exception as exc:
                    # A.60：审计写失败原先静默 —— 紧急签名的"验签失败"必须可追溯
                    logger.warning(
                        "emergency_verify_failed_audit_write_failed",
                        report_id=report_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                return False

            try:
                from app.config.redis import RedisPool
                from app.config.redis_keys import RedisKey
                redis_client = await RedisPool.get_client()
                await redis_client.delete(RedisKey.hsm_emergency_pending_verification(report_id))
            except Exception as exc:
                # A.60：清理待验键失败原先静默（残留键会让下一次验签看到过期状态）
                logger.warning(
                    "emergency_pending_verification_cleanup_failed",
                    report_id=report_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            try:
                from app.services.audit_service import AuditService
                audit_svc = AuditService()
                await audit_svc.log_action(
                    action="EMERGENCY_SIGNATURE_VERIFIED",
                    actor="system",
                    target_type="report",
                    target_id=report_id,
                    detail={"verification_result": "success"},
                )
            except Exception as exc:
                # A.60：验签通过也要留痕（审计链是"谁在什么情况下签的"唯一依据）
                logger.warning(
                    "emergency_verified_audit_write_failed",
                    report_id=report_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            logger.info("emergency_signature_verified", report_id=report_id)
            return True

        except Exception as e:
            logger.critical("emergency_signature_verification_failed",
                           report_id=report_id, error=str(e))

            try:
                from app.services.audit_service import AuditService
                audit_svc = AuditService()
                await audit_svc.log_action(
                    action="EMERGENCY_SIGNATURE_VERIFICATION_FAILED",
                    actor="system",
                    target_type="report",
                    target_id=report_id,
                    detail={"error": str(e)},
                )
            except Exception as audit_exc:
                # A.60：异常路径的审计写失败也要留痕（否则"验签崩了"连审计都没有）
                logger.warning(
                    "emergency_verify_error_audit_write_failed",
                    report_id=report_id,
                    error=str(audit_exc),
                    error_type=type(audit_exc).__name__,
                )

            return False


_emergency_signing_instance: EmergencySigningManager | None = None
_emergency_signing_lock = threading.Lock()


def get_emergency_signing_manager() -> EmergencySigningManager:
    global _emergency_signing_instance
    if _emergency_signing_instance is None:
        with _emergency_signing_lock:
            if _emergency_signing_instance is None:
                _emergency_signing_instance = EmergencySigningManager()
    return _emergency_signing_instance
