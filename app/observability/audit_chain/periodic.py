"""审计链周期任务：完整性校验 + **跨链锚存证**（A.119 / G44）。

## 缺口（实测）

1. `start_audit_chain_periodic_tasks()` 在 `app/` 内**零调用** —— 周期校验与跨链锚都不跑；
2. 即使跑起来，函数体里 `cross_chain = AuditCrossChain(db)` 只是**构造**，
   `record_cross_chain()` **从未被调用** —— "跨链锚存证"能力等于不存在；
3. `async for db in db_session_factory()` 只对"异步生成器"成立；传 `AsyncSessionLocal`
   （sessionmaker）会直接抛 `TypeError` —— 接线时必然踩到。

## 现在的行为

每个周期：先 `verify_all()`（断链 → critical 日志），再在**同一窗口**
`[now-interval, now]` 上写一条跨链锚（带上一锚的 hash，形成链）。
锚写入失败只记 error，不终止循环（否则"发现断链"的能力会一起消失）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.config.settings import settings

from .cross_chain import AuditCrossChain
from .verifier import AuditChainVerifier

_logger = structlog.get_logger("app.observability.audit_chain.periodic")
_periodic_task: asyncio.Task | None = None
_periodic_running = False


async def _latest_cross_hash(db: Any) -> str | None:
    """取上一条跨链锚的 hash（没有则 None，表示这条是创世锚）。"""
    from sqlalchemy import select

    from app.models.database import AuditCrossChain as AuditCrossChainModel

    result = await db.execute(
        select(AuditCrossChainModel.cross_hash_self)
        .order_by(AuditCrossChainModel.created_at.desc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def run_audit_chain_cycle(
    db: Any,
    *,
    window_hours: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """跑一轮：校验 + 写跨链锚。返回本轮摘要（便于日志与测试断言）。"""
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

    end = now or datetime.now(timezone.utc)
    start = end - timedelta(hours=window_hours)
    prev_hash = await _latest_cross_hash(db)
    # A.119 / G44：这里以前只是**构造** AuditCrossChain 就结束，"存证"从未发生。
    anchor = await AuditCrossChain(db).record_cross_chain(start, end, prev_hash)
    await db.commit()
    return {
        "all_valid": results["all_valid"],
        "anchor": anchor,
        "prev_cross_hash": prev_hash,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


async def _loop(
    db_session_factory: Any,
    *,
    window_hours: float,
    interval_s: float,
    stop: asyncio.Event | None = None,
) -> None:
    """周期循环（提出来是为了可单测：间隔用秒传，不用等一小时）。"""
    global _periodic_running
    while _periodic_running:
        if stop is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
                return
            except asyncio.TimeoutError:  # silent-ok: 等满一个间隔是正常轮次边界
                pass
        else:
            await asyncio.sleep(interval_s)
        try:
            async with db_session_factory() as db:
                await run_audit_chain_cycle(db, window_hours=window_hours)
        except Exception as e:  # silent-ok: 单轮失败（DB 抖动）不得让周期任务退出，下一轮继续
            _logger.error(
                "periodic_audit_verification_failed",
                error=str(e)[:160],
                error_type=type(e).__name__,
            )


async def start_audit_chain_periodic_tasks(
    db_session_factory,
    interval_hours: int | None = None,
) -> None:
    """启动周期任务。

    ``db_session_factory`` 必须是 **sessionmaker**（如 ``AsyncSessionLocal``），
    不是 ``get_db_session`` 这类异步生成器 —— 后者不能 `async with`。
    """
    global _periodic_task, _periodic_running
    if _periodic_running:
        return
    _periodic_running = True
    hours = interval_hours or settings.obs_audit_cross_chain_interval_hours

    _periodic_task = asyncio.create_task(
        _loop(db_session_factory, window_hours=hours, interval_s=hours * 3600)
    )
    _logger.info("cross_chain_periodic_task_started", interval_hours=hours)


async def stop_audit_chain_periodic_tasks() -> None:
    global _periodic_running, _periodic_task
    _periodic_running = False
    if _periodic_task:
        _periodic_task.cancel()
        try:
            await _periodic_task
        except asyncio.CancelledError:  # silent-ok: 周期性任务取消属正常停机
            pass
        _periodic_task = None
    _logger.info("cross_chain_periodic_task_stopped")
