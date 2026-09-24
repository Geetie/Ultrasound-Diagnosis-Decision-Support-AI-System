"""T-G1-SEC-02 — 脱敏还原服务访问频率监控。

规格（``04-安全合规.md``）：
- compact 模式单小时还原 > 100 次触发安全告警；
- full 模式单小时还原 > 200 次触发安全告警；
- 阈值经 ``DESENSITIZATION_RESTORE_RATE_LIMIT`` 配置；
- 告警记录至审计日志并通知数据安全官。

与既有 ``DesensitizationRestoreService._check_rate_limit_persistent`` 的分工：
后者负责**拦截**（超限即拒绝还原），本模块负责**告警链路**（审计 + 通知）。
频率限制此前只打了一条 warning 日志，既没有审计留痕也没有通知——安全事件
"看不见"等于不存在。

设计要点：
1. **同一小时同一档位只告警一次**：超限后每个请求都告警会造成审计洪泛，
   反而掩盖真正的异常；去重键优先用 Redis（多实例共享），不可用时退化为
   进程内集合。
2. **审计与通知都不阻断业务**：告警链路自身失败只记日志，绝不影响还原
   请求的返回（可用性优先于可观测性，但会额外记一条 ``*_failed``）。
3. 数据安全官的通知走 :class:`SecurityOfficerNotifier` 协议：默认只落日志
   （离线可用），配置 webhook 后走 HTTP。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol, Set, runtime_checkable

import structlog

from app.config.settings import settings

logger = structlog.get_logger("app.security.restore_rate_monitor")

# 审计动作名（与 04-安全合规 的审计动作表一致）
AUDIT_ACTION = "desensitization_restore_rate_limit_exceeded"
NOTIFY_ACTION = "desensitization_restore_rate_limit_notified"


@dataclass
class RestoreRateAlert:
    """一次超限告警。"""

    profile: str
    hour_key: str
    count: int
    limit: int
    detected_at: datetime
    actors: Set[str] = field(default_factory=set)
    notified: bool = False
    audit_logged: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile,
            "hour_key": self.hour_key,
            "count": self.count,
            "limit": self.limit,
            "exceeded_by": max(0, self.count - self.limit),
            "detected_at": self.detected_at.isoformat(),
            "actors": sorted(self.actors),
            "notified": self.notified,
            "audit_logged": self.audit_logged,
        }


@runtime_checkable
class SecurityOfficerNotifier(Protocol):
    """数据安全官通知通道。"""

    @property
    def name(self) -> str:
        ...

    async def notify(self, alert: RestoreRateAlert) -> bool:
        """通知数据安全官；返回是否成功送出。"""
        ...


class LoggingSecurityOfficerNotifier:
    """默认通知通道：结构化日志（离线/单机部署可用，可被告警平台采集）。"""

    @property
    def name(self) -> str:
        return "logging"

    async def notify(self, alert: RestoreRateAlert) -> bool:
        logger.warning(
            "data_security_officer_notified",
            **alert.as_dict(),
            channel=self.name,
        )
        return True


class WebhookSecurityOfficerNotifier:
    """HTTP 通知通道（对接医院安全运营平台/工单系统）。"""

    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        if not url:
            raise ValueError("WebhookSecurityOfficerNotifier requires a URL")
        self._url = url
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "webhook"

    async def notify(self, alert: RestoreRateAlert) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    json={"type": AUDIT_ACTION, **alert.as_dict()},
                )
            return 200 <= response.status_code < 300
        except Exception as exc:  # noqa: BLE001 - 通知失败不阻断业务
            logger.error(
                "security_officer_notify_failed",
                channel=self.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False


class RestoreRateMonitor:
    """脱敏还原频率监控与告警。

    Args:
        profile: 部署档位（compact/full）；缺省取 ``settings.deployment_profile``。
        hour_limit: 显式覆盖阈值（缺省按档位取 settings）。
        notifier: 数据安全官通知通道；缺省按配置选择。
    """

    def __init__(
        self,
        *,
        profile: Optional[str] = None,
        hour_limit: Optional[int] = None,
        notifier: Optional[SecurityOfficerNotifier] = None,
    ) -> None:
        self._profile_override = profile
        self._hour_limit_override = hour_limit
        self._notifier = notifier
        # 进程内去重（Redis 不可用时的兜底），键为 (profile, hour_key)
        self._alerted: Set[tuple] = set()
        self._alerts: list = []

    # ---- 阈值 ----

    @property
    def profile(self) -> str:
        return self._profile_override or str(settings.deployment_profile)

    @property
    def hour_limit(self) -> int:
        if self._hour_limit_override is not None:
            return self._hour_limit_override
        # settings 的区间校验器已按档位选好有效阈值
        # （full 模式下会用 desensitization_restore_rate_limit_full）
        return int(settings.desensitization_restore_rate_limit)

    def thresholds(self) -> Dict[str, Any]:
        """对外暴露阈值口径（运维面板与合规检查用）。"""
        return {
            "profile": self.profile,
            "effective_hour_limit": self.hour_limit,
            "compact_hour_limit": int(settings.desensitization_restore_rate_limit),
            "full_hour_limit": int(settings.desensitization_restore_rate_limit_full),
            "redis_backed": True,
        }

    def is_breaching(self, count: int) -> bool:
        return count > self.hour_limit

    # ---- 主入口 ----

    async def evaluate(
        self,
        *,
        count: int,
        hour_key: Optional[str] = None,
        actor: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Optional[RestoreRateAlert]:
        """超限则触发一次告警（同小时同档位去重）。

        Returns:
            新产生的告警；未超限或已告警过则为 ``None``。
        """
        effective_limit = int(limit or self.hour_limit)
        key_hour = hour_key or datetime.now(timezone.utc).strftime("%Y%m%d%H")
        if count <= effective_limit:
            return None

        dedup_key = (self.profile, key_hour)
        if not await self._acquire_alert_slot(dedup_key):
            return None

        alert = RestoreRateAlert(
            profile=self.profile,
            hour_key=key_hour,
            count=count,
            limit=effective_limit,
            detected_at=datetime.now(timezone.utc),
            actors={actor} if actor else set(),
        )

        alert.audit_logged = await self._write_audit(alert, actor=actor, session_id=session_id)
        alert.notified = await self._notify(alert)
        self._alerts.append(alert)

        logger.warning("desensitization_restore_rate_limit_alert", **alert.as_dict())
        return alert

    # ---- 内部 ----

    async def _acquire_alert_slot(self, dedup_key: tuple) -> bool:
        """争取"本小时可告警"的名额（Redis 优先，进程内兜底）。"""
        profile, hour_key = dedup_key
        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey

            client = await RedisPool.get_client()
            # SET NX EX：第一个请求拿到名额，其余返回 None
            acquired = await client.set(
                RedisKey.desens_restore_alert(profile, hour_key),
                "1",
                nx=True,
                ex=7200,
            )
            return bool(acquired)
        except Exception as exc:  # noqa: BLE001 - Redis 不可用时退化为进程内去重
            logger.debug("restore_rate_alert_dedup_redis_failed", error=str(exc))
            if dedup_key in self._alerted:
                return False
            self._alerted.add(dedup_key)
            return True

    async def _write_audit(
        self,
        alert: RestoreRateAlert,
        *,
        actor: Optional[str],
        session_id: Optional[str],
    ) -> bool:
        """写审计（独立会话，失败不影响业务与告警）。"""
        try:
            from app.config.database import AsyncSessionLocal
            from app.services.audit_service import AuditService

            async with AsyncSessionLocal() as db:
                service = AuditService(db)
                await service.log_action(
                    action=AUDIT_ACTION,
                    actor=actor or "system",
                    session_id=session_id,
                    target_type="desensitization_restore",
                    target_id=alert.hour_key,
                    detail=alert.as_dict(),
                    log_type="security",
                )
                commit = getattr(db, "commit", None)
                if commit is not None:
                    import inspect

                    maybe = commit()
                    if inspect.isawaitable(maybe):
                        await maybe
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "restore_rate_alert_audit_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                **alert.as_dict(),
            )
            return False

    async def _notify(self, alert: RestoreRateAlert) -> bool:
        notifier = self._notifier or self._build_notifier()
        try:
            sent = await notifier.notify(alert)
        except Exception as exc:  # noqa: BLE001 - 通知通道异常不阻断业务
            logger.error(
                "restore_rate_alert_notify_failed",
                channel=getattr(notifier, "name", "unknown"),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False
        logger.info(
            NOTIFY_ACTION,
            channel=getattr(notifier, "name", "unknown"),
            sent=sent,
            **alert.as_dict(),
        )
        return bool(sent)

    def _build_notifier(self) -> SecurityOfficerNotifier:
        webhook = str(getattr(settings, "security_officer_webhook", "") or "")
        if webhook:
            return WebhookSecurityOfficerNotifier(webhook)
        return LoggingSecurityOfficerNotifier()

    # ---- 观测 ----

    def stats(self) -> Dict[str, Any]:
        """监控总览（运维面板与合规检查用）。"""
        return {
            **self.thresholds(),
            "alerts_total": len(self._alerts),
            "last_alert": self._alerts[-1].as_dict() if self._alerts else None,
            "notifier": (self._notifier or self._build_notifier()).name,
        }


_monitor: Optional[RestoreRateMonitor] = None


def get_restore_rate_monitor() -> RestoreRateMonitor:
    """取得进程级监控实例。"""
    global _monitor
    if _monitor is None:
        _monitor = RestoreRateMonitor()
    return _monitor


def reset_restore_rate_monitor() -> None:
    """重置单例（测试用）。"""
    global _monitor
    _monitor = None


__all__ = [
    "AUDIT_ACTION",
    "NOTIFY_ACTION",
    "LoggingSecurityOfficerNotifier",
    "RestoreRateAlert",
    "RestoreRateMonitor",
    "SecurityOfficerNotifier",
    "WebhookSecurityOfficerNotifier",
    "get_restore_rate_monitor",
    "reset_restore_rate_monitor",
]
