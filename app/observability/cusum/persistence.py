"""02-5 链B：AI 安全熔断器（AISafetyCircuitBreaker）+ CUSUM 累计量的状态持久化。

背景（02-5②-3 / 反证腿 BR4）：熔断状态、CUSUM 的 s_plus/s_minus/
total_observations 全在进程内存（circuit_breaker.py:42-52 / monitor.py:46），
服务重启即失忆 —— "连续重启即永不熔断"是可被利用的缺口。文档 §02-5③
YAGNI 清单原列"不做持久化"，本轮按用户拍板补上（语义(a) 降级版的前提下，
持久化是熔断读者可信的前提）。

设计（最小可行，复用既有 RedisPool/RedisKey 模式，不自造新存储）：
- **单键 JSON 快照**：breaker 三态 + CUSUM 累计量，
  键 = ``RedisKey.AI_SAFETY_BREAKER_STATE``；
- **写**：状态迁移处（``_request_suspension`` / ``check_pending_timeout`` /
  ``cancel_pending_suspension`` / ``resume``）与 CUSUM 每次 ``update()`` 后
  fire-and-forget 落一次（量级 ~1 次 SET/判读，可接受）；
- **读**：进程启动（main.py lifespan）restore 一次；
- **失败安全**：Redis 不可用 → 写失败仅 warning（内存状态机照常工作）；
  读失败/无快照 → 保持 ACTIVE（与现状一致，不比现状更差）；
- **TTL 24h**：断流超一天的快照视为过期。pending 的 900s 确认期限与
  SUSPENDED 的 14400s 安审期限都是**绝对时间戳**，恢复后
  ``check_pending_timeout`` / ``check_safety_review_timeout`` 仍按原期限
  自然到期，无需额外补偿逻辑；
- **单进程前提**：与 circuit_breaker 进程内单例同前提（main.py:73-75，
  WEB_CONCURRENCY=1）；多 worker 下会退化为"每 worker 一份快照、后写覆盖"，
  与现状一致，不引入分布式锁（登记为已知边界）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from app.config.redis_keys import RedisKey

_LOGGER = structlog.get_logger("app.observability.cusum.persistence")

_STATE_TTL_SECONDS = 86400  # 24h：断流超一天的快照视为过期

#: CUSUM 累计量里需要跨重启存活的字段（CUSUMState 的可回灌子集）
_MONITOR_FIELDS = (
    "s_plus",
    "s_minus",
    "total_observations",
    "total_severe",
    "alert_triggered",
)


def snapshot_breaker_state(breaker: Any) -> dict[str, Any]:
    """抓取 breaker + 其 CUSUM monitor 的可持久化快照（同步、纯读）。"""
    info = breaker.suspension_info
    monitor_state = breaker._monitor.get_state()
    state = info.get("state")
    return {
        "state": getattr(state, "value", state),
        "suspended_at": info.get("suspended_at"),
        "reason": info.get("reason"),
        "pending_reason": info.get("pending_reason"),
        "pending_since": info.get("pending_since"),
        "monitor": {k: monitor_state.get(k) for k in _MONITOR_FIELDS},
        "saved_at": time.time(),
    }


async def persist_breaker_state(breaker: Any) -> bool:
    """把快照写入 Redis（SETEX 24h）。失败仅 warning，不影响内存状态机。"""
    try:
        from app.config.redis import RedisPool

        client = await RedisPool.get_client()
        payload = json.dumps(snapshot_breaker_state(breaker))
        await client.setex(
            RedisKey.AI_SAFETY_BREAKER_STATE, _STATE_TTL_SECONDS, payload
        )
        return True
    except Exception as exc:
        _LOGGER.warning("cusum_state_persist_failed", error=str(exc))
        return False


def schedule_breaker_state_persist(breaker: Any | None = None) -> None:
    """fire-and-forget 落盘。无运行中的事件循环（同步测试/启动早期）时静默跳过。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # silent-ok: 无事件循环（同步单测/启动早期）本就无处落盘
        return
    if breaker is None:
        from .circuit_breaker import AISafetyCircuitBreaker

        breaker = AISafetyCircuitBreaker.get_instance()
    loop.create_task(persist_breaker_state(breaker))


async def restore_breaker_state(breaker: Any) -> bool:
    """进程启动时回灌。无快照/读失败/回灌失败 → 保持 ACTIVE（不比现状差）。"""
    try:
        from app.config.redis import RedisPool

        client = await RedisPool.get_client()
        raw = await client.get(RedisKey.AI_SAFETY_BREAKER_STATE)
        if not raw:
            return False
        payload = json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception as exc:
        _LOGGER.warning("cusum_state_restore_read_failed", error=str(exc))
        return False

    try:
        from .circuit_breaker import CircuitBreakerState

        state = payload.get("state")
        if state == CircuitBreakerState.SUSPENDED.value:
            breaker.restore_state(
                CircuitBreakerState.SUSPENDED,
                reason=payload.get("reason"),
                suspended_at=payload.get("suspended_at"),
            )
        elif state == CircuitBreakerState.PENDING_CONFIRMATION.value:
            breaker.restore_state(
                CircuitBreakerState.PENDING_CONFIRMATION,
                pending_reason=payload.get("pending_reason"),
                pending_since=payload.get("pending_since"),
            )
        monitor_payload = payload.get("monitor") or {}
        if monitor_payload:
            breaker._monitor.restore_state(**monitor_payload)
        _LOGGER.info(
            "cusum_state_restored",
            state=state,
            monitor={k: monitor_payload.get(k) for k in _MONITOR_FIELDS},
        )
        return True
    except Exception as exc:
        _LOGGER.warning("cusum_state_restore_apply_failed", error=str(exc))
        return False
