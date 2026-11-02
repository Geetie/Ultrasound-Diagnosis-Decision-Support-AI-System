"""熔断事件 → 审计留痕（A.113 / G20）。

## 缺口

`AISafetyCircuitBreaker._notify_suspension_callbacks()` 会广播
`suspended` / `pending_confirmation`，但 `register_suspension_callback()` **生产零订阅**
（全仓只有测试调用）。后果：熔断"发生过"只留在进程日志与告警里，**不进审计库**——
事后查"这台机器什么时候停过 AI 辅助、为什么"，只能翻日志文件；而恢复动作
（`resume()`）反而有留痕（log storage + 端点审计），造成"恢复有记录、触发没记录"的倒挂。

## 设计

- 订阅回调用**同步签名**（状态机在锁内/同步上下文里广播），内部转成一次性 asyncio task
  写库；**没有运行中的事件循环**（同步测试/启动早期）时退化为 log storage 留痕，
  并打 warning——不抛、不阻断状态迁移（熔断是安全动作，不能被审计链路拖住）。
- 写库用独立短会话（AGENTS 硬规则 #6：会话不跨 LLM 调用；这里更简单——只在写库段内）。
- 审计 `action` 固定为 `ai_safety_breaker_<event>`，`target_type=safety_breaker`，
  detail 带 reason；失败只记 warning（审计失败不能反向影响熔断状态）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import structlog

_logger = structlog.get_logger("app.observability.cusum.audit_bridge")


async def write_breaker_audit(event: str, reason: str, *, audit_service: Any | None = None) -> bool:
    """写一条熔断审计；返回是否写入成功（失败不抛）。"""
    try:
        if audit_service is None:
            from app.config.database import AsyncSessionLocal
            from app.services.audit_service import AuditService

            async with AsyncSessionLocal() as db:
                audit_service = AuditService(db)
                await audit_service.log_action(
                    action=f"ai_safety_breaker_{event}",
                    actor="system:custody",
                    target_type="safety_breaker",
                    target_id="ai_safety_circuit_breaker",
                    detail={"event": event, "reason": reason},
                )
                await db.commit()
                return True
        await audit_service.log_action(
            action=f"ai_safety_breaker_{event}",
            actor="system:custody",
            target_type="safety_breaker",
            target_id="ai_safety_circuit_breaker",
            detail={"event": event, "reason": reason},
        )
        return True
    except Exception as exc:  # silent-ok: 审计失败必须只留 warning，不能反向影响熔断状态
        _logger.warning(
            "cusum_breaker_audit_failed",
            breaker_event=event,
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return False


def _fallback_log(event: str, reason: str) -> None:
    """无事件循环时的退化留痕（log storage 与 resume() 同一出口）。"""
    try:
        from app.observability.enums import LogCategory
        from app.observability.logging.storage import get_log_storage

        get_log_storage().write(
            category=LogCategory.AUDIT,
            message=f"AI辅助熔断事件: event={event}, reason={reason}",
            extra={"action": f"ai_safety_breaker_{event}", "reason": reason},
        )
    except Exception as exc:  # silent-ok: 退化留痕也失败时只剩 warning，不能再抛
        _logger.warning("cusum_breaker_audit_fallback_failed", error=str(exc)[:160])


def make_suspension_audit_callback(
    *, audit_service_factory: Callable[[], Any] | None = None
) -> Callable[[str, str], None]:
    """构造订阅回调：有事件循环 → 异步写库；否则 → log storage 退化。"""

    def _callback(event: str, reason: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # silent-ok: 同步上下文/启动早期没有事件循环 → 走退化留痕
            _fallback_log(event, reason)
            return
        if audit_service_factory is not None:
            loop.create_task(write_breaker_audit(event, reason, audit_service=audit_service_factory()))
        else:
            loop.create_task(write_breaker_audit(event, reason))

    return _callback


def register_breaker_audit_subscriber(breaker: Any, **kwargs: Any) -> Callable[[str, str], None]:
    """把审计订阅挂到熔断器上（**幂等**：重复调用不会重复写审计）。

    幂等靠熔断器上的标记位，而不是"比较闭包对象"——每次调用都会产生新闭包，
    对象比较永远不相等（首版就是这么错的，测试当场抓到）。
    """
    existing_cb = getattr(breaker, "_audit_subscriber_callback", None)
    if callable(existing_cb):
        return existing_cb
    callback = make_suspension_audit_callback(**kwargs)
    breaker.register_suspension_callback(callback)
    try:
        breaker._audit_subscriber_callback = callback
    except AttributeError:  # silent-ok: 只读替身也能注册，只是失去幂等标记
        _logger.warning("cusum_breaker_audit_marker_skipped")
    _logger.info("cusum_breaker_audit_subscriber_registered")
    return callback
