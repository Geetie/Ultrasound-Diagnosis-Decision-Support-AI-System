"""Safety Gateway同意状态校验器模块

负责患者同意状态的校验逻辑，包括数据库查询、重试机制和熔断器保护。

降级策略（严格对齐01-系统架构设计§3.4 + 04-安全合规§6）：
- 唯一路径：直查PostgreSQL获取最新同意状态，不使用任何缓存
- PostgreSQL不可用时：直接返回错误（E4005B），拒绝所有诊断输出
- 设计依据：01-系统架构设计§3.4明确要求"SafetyGateway拦截点始终直查PostgreSQL，
  不使用任何缓存"，并给出安全论证——"500ms缓存窗口内，若患者撤回同意，
  缓存仍为consented，SafetyGateway复用缓存则漏过撤回。对于医疗安全关键路径，
  概率论证不够充分——即使500ms远短于诊断处理时间，仍存在确定性安全漏洞。"

注意：04-安全合规.md中E4005B描述提到"降级为Redis只读缓存(TTL 60s)"，
此为二期预留策略。一期严格遵循01-系统架构设计§3.4，不降级为Redis缓存。
代码实现以01-系统架构设计§3.4为准——更严格的策略优先。
"""

import asyncio
import threading
from typing import NamedTuple

import structlog
from sqlalchemy import select

from safety_gateway.circuit_breaker import circuit_breaker_manager
from safety_gateway.errors import (
    CircuitBreakerError,
    DatabaseConnectionError,
    DatabaseError,
    DatabaseQueryError,
    DatabaseQueryTimeoutError,
)
from safety_gateway.retry import retry_with_backoff
from safety_gateway.sg_config import sg_settings as settings
from safety_gateway.sg_db import _get_async_session_local
from safety_gateway.sg_models import ConsentRecord
from safety_gateway.validation_result import ConsentStatus

logger = structlog.get_logger()


class ConsentCheckResult(NamedTuple):
    status: ConsentStatus | None
    timeout: bool
    error: bool


class ConsentChecker:

    def __init__(self):
        self._query_timeout = settings.db_query_timeout
        self._retry_max_attempts = settings.sg_db_retry_max_attempts
        self._retry_delay_base = settings.sg_db_retry_delay_base
        self._retry_max_total_time = settings.sg_db_retry_max_total_time_seconds

    async def _query_consent_status(self, patient_id: str) -> ConsentStatus:
        async with asyncio.timeout(self._query_timeout):
            session_local = await _get_async_session_local()
            async with session_local() as session:
                result = await session.execute(
                    select(ConsentRecord)
                    .where(ConsentRecord.patient_id == patient_id)
                    .order_by(ConsentRecord.created_at.desc())
                    .limit(1)
                )
                consent_record = result.scalar_one_or_none()

                if consent_record:
                    return ConsentStatus(consent_record.status)
                logger.warning(
                    "consent_record_not_found_in_db",
                    patient_id=patient_id,
                    note="No consent record — treating as DENIED per 03-接口规范§5.1 (no record = not consented → E1008)",
                )
                return ConsentStatus.DENIED

    async def _retry_query(self, patient_id: str) -> ConsentStatus:
        try:
            return await retry_with_backoff(
                self._query_consent_status,
                patient_id,
                max_attempts=self._retry_max_attempts,
                delay_base=self._retry_delay_base,
                max_total_time=self._retry_max_total_time,
                retryable_exceptions=(asyncio.TimeoutError, DatabaseError, OSError),
                log_prefix="consent_check",
            )
        except asyncio.TimeoutError:
            raise DatabaseQueryTimeoutError(
                f"同意状态查询超时，已重试{self._retry_max_attempts}次"
            )
        except (DatabaseError, OSError) as e:
            raise DatabaseQueryError(f"同意状态查询失败: {e}")

    async def check_consent_status(self, patient_id: str) -> ConsentCheckResult:
        breaker = circuit_breaker_manager.get_circuit_breaker(
            "sg_db_consent",
            failure_threshold=settings.sg_circuit_breaker_failure_threshold,
            success_threshold=settings.sg_circuit_breaker_success_threshold,
            cooldown_period=settings.sg_circuit_breaker_cooldown_period,
        )

        try:
            consent_status = await breaker.execute(self._retry_query, patient_id)
            return ConsentCheckResult(consent_status, False, False)

        except CircuitBreakerError:
            logger.error(
                "consent_check_circuit_breaker_open_db_unavailable",
                patient_id=patient_id,
                note="SG直查PG策略：PG不可用时拒绝所有诊断输出(E4005B)，不降级为Redis缓存",
            )
            return ConsentCheckResult(None, False, True)

        except DatabaseQueryTimeoutError:
            logger.error(
                "consent_check_timeout_db_unavailable",
                patient_id=patient_id,
                note="SG直查PG策略：PG查询超时，返回DB_TIMEOUT降级",
            )
            return ConsentCheckResult(None, True, False)

        except (DatabaseConnectionError, DatabaseQueryError) as e:
            logger.error(
                "consent_check_db_error",
                error=str(e),
                patient_id=patient_id,
                note="SG直查PG策略：PG不可用时拒绝所有诊断输出(E4005B)，不降级为Redis缓存",
            )
            return ConsentCheckResult(None, False, True)

        except Exception as e:
            logger.error(
                "consent_check_unexpected_error",
                error=str(e),
                patient_id=patient_id,
                note="SG直查PG策略：未知异常视为DB不可用",
            )
            return ConsentCheckResult(None, False, True)

    async def close(self):
        logger.info("consent_checker_closed")


from safety_gateway.registry import sg_registry

_singleton_lock = threading.Lock()


def get_consent_checker() -> ConsentChecker:
    if not sg_registry.is_registered("consent_checker"):
        with _singleton_lock:
            if not sg_registry.is_registered("consent_checker"):
                sg_registry.register("consent_checker", ConsentChecker)
    return sg_registry.get("consent_checker")


def _reset_consent_checker() -> None:
    sg_registry.reset("consent_checker")
