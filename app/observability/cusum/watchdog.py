"""CUSUM 熔断状态看门狗（A.109 / N1）：让 pending 能自然到期晋级。

## 为什么需要它（实测缺口）

`AISafetyCircuitBreaker` 的三态机里，`PENDING_CONFIRMATION → SUSPENDED` 的晋级
只发生在 `check_pending_timeout()` 被调用时。全仓 grep 显示该方法**只有测试调用**、
没有调度器（G16），于是：

1. 一旦进入 pending，`pipeline.py` 会按 pending 把每例判读标 `needs_review`；
2. pending 永不过期 → 永远停在"待确认"，`SUSPENDED` 与安审时限都不触发；
3. `persistence.py` 又把 pending 快照恢复回来（24h TTL）→ 重启也退不出去。

## 设计依据（不以自拟口径发明状态机）

成熟实现的做法是同一件事：把"计时晋级"交给一个**独立周期任务**，而不是等
业务请求顺带触发（Resilience4j 的 `CircuitBreaker` 用时间戳在每次调用时判定，
Sentinel 用独立 metric 写入 + 规则检查；两者的共同点是**判定有明确时钟来源、
且状态迁移必然被评估**）。这里选"独立周期任务"而不是"业务调用时顺便判定"：

- 业务侧判定会把熔断语义耦合进诊断路径（该路径已很重），且没有请求时状态就卡住；
- 周期任务的时钟与 `check_pending_timeout()` 内部同一来源（`time.time()`），不引入第二套时间。

## 行为契约

- 间隔由 `settings.cusum_pending_sweep_seconds` 控制（≤0 表示关闭本任务）；
- 每次只调 `check_pending_timeout()`，**不**推进 SUSPENDED 的安审期限（那是人工动作，
  由 `check_safety_review_timeout()` 负责，本循环不替人做决定）；
- 单次异常只记日志、不终止循环（看门狗自己挂掉等于缺口复发）；
- 任务可被取消（lifespan 关闭时 `task.cancel()`）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import structlog

from app.config.settings import settings

_logger = structlog.get_logger("app.observability.cusum.watchdog")

DEFAULT_SWEEP_SECONDS = 30.0


class _SupportsPendingTimeout(Protocol):
    def check_pending_timeout(self) -> bool: ...


def sweep_seconds() -> float:
    """当前扫描间隔（秒）；`<=0` 表示禁用（返回 0.0）。"""
    raw = getattr(settings, "cusum_pending_sweep_seconds", DEFAULT_SWEEP_SECONDS)
    try:
        value = float(raw)
    except (TypeError, ValueError):  # silent-ok: 配置异常按禁用处理并告警（下面统一记）
        value = 0.0
    return value if value > 0 else 0.0


def sweep_once(breaker: _SupportsPendingTimeout) -> bool:
    """执行一次晋级判定；返回是否真的发生了 `pending → suspended`。"""
    return bool(breaker.check_pending_timeout())


async def run_pending_timeout_watchdog(
    breaker: _SupportsPendingTimeout,
    *,
    interval_s: float | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """周期执行 `sweep_once`，直到被取消或 `stop` 置位。"""
    interval = sweep_seconds() if interval_s is None else float(interval_s)
    if interval <= 0:
        _logger.info("cusum_pending_watchdog_disabled", interval_s=interval)
        return
    _logger.info("cusum_pending_watchdog_started", interval_s=interval)
    while True:
        if stop is not None and stop.is_set():
            _logger.info("cusum_pending_watchdog_stopped")
            return
        try:
            if sweep_once(breaker):
                _logger.critical("cusum_pending_timeout_escalated_to_suspended")
        except Exception as exc:  # silent-ok: 单次失败不能让看门狗自己退出（否则缺口复发）
            _logger.warning(
                "cusum_pending_watchdog_sweep_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        try:
            if stop is None:
                await asyncio.sleep(interval)
            else:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            _logger.info("cusum_pending_watchdog_cancelled")
            raise


def start_watchdog(app: Any, breaker: _SupportsPendingTimeout | None = None) -> asyncio.Task | None:
    """在 lifespan 里启动看门狗；返回 task（便于关闭时取消）。"""
    if sweep_seconds() <= 0:
        _logger.info("cusum_pending_watchdog_not_started", reason="interval<=0")
        return None
    if breaker is None:
        from app.observability.cusum.circuit_breaker import AISafetyCircuitBreaker

        breaker = AISafetyCircuitBreaker.get_instance()
    task = asyncio.create_task(run_pending_timeout_watchdog(breaker))
    app.state.cusum_watchdog_task = task
    return task
