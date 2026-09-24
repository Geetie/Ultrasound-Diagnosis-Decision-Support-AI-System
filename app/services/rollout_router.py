"""T-GR-INT-01：灰度发布流量路由。

验收口径
--------
- ``full`` 档位：按 **5% 流量**放量；
- ``compact`` 档位：按会话数爬坡——首个会话 → 10 个会话 → 全量；
- 灰度流量路由正确（同一会话恒定命中同一桶，可复现、可回放）。

两个刻意的设计选择
------------------
1. **确定性哈希分桶，不用随机数**：同一 ``session_id`` 每次都必须落到同一桶，
   否则同一个患者在一次检查里会一会儿走新行为一会儿走旧行为，结果不可复现，
   出了问题也无法回放定位。
2. **缺 session_id 时 fail-open（放行）**：灰度的目的是控制新行为暴露面，不是
   拒绝诊疗。因为拿不到分桶依据就把诊断请求拦下来（尤其本项目是临床系统）
   属于本末倒置；此时记 ``missing_session_id`` 便于事后统计这类请求的占比。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

import structlog

logger = structlog.get_logger()

BUCKET_COUNT = 100


class RolloutTrack(str, Enum):
    """灰度轨道（与响应档位一一对应）。"""

    FULL = "full"
    COMPACT = "compact"


class RolloutStage(str, Enum):
    OFF = "off"              # 灰度关闭
    CANARY = "canary"        # 首个会话
    RAMP = "ramp"            # 爬坡中（≤ 阈值会话数）
    COMPLETE = "complete"    # 爬坡完成，全量


@dataclass(frozen=True)
class RolloutDecision:
    allowed: bool
    track: str
    stage: str
    reason: str
    bucket: Optional[int] = None
    session_index: int = 0
    percent: int = 0

    def as_dict(self) -> Dict[str, object]:
        return {
            "allowed": self.allowed,
            "track": self.track,
            "stage": self.stage,
            "reason": self.reason,
            "bucket": self.bucket,
            "session_index": self.session_index,
            "percent": self.percent,
        }


def bucket_of(session_id: str) -> int:
    """把 session_id 确定性地映射到 [0, BUCKET_COUNT) 的桶。"""
    digest = hashlib.sha256(session_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % BUCKET_COUNT


class RolloutSessionCounter:
    """灰度会话计数（Redis 优先，不可用时退化为进程内计数）。

    Redis 不可用时不抛错：灰度计数只是放量依据，不应因此中断诊断。退化到进程内
    计数会让多副本下的序号偏小（各副本独立计数），这一点在日志里标注出来。
    """

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._memory: Dict[str, int] = {}

    async def _ensure_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            from app.config.redis import RedisPool

            self._redis = await RedisPool.get_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("rollout_counter_redis_unavailable", error=str(exc))
            self._redis = None
        return self._redis

    async def next_index(self, track: str) -> int:
        """返回本会话在该轨道中的序号（1-based）。"""
        redis_client = await self._ensure_redis()
        if redis_client is not None:
            try:
                from app.config.redis_keys import RedisKey
                from app.config.settings import settings

                key = RedisKey.rollout_session_counter(track)
                index = await redis_client.incr(key)
                if index == 1:
                    await redis_client.expire(
                        key, settings.rollout_counter_ttl_seconds
                    )
                return int(index)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "rollout_counter_redis_failed_fallback_memory",
                    track=track,
                    error=str(exc),
                )

        self._memory[track] = self._memory.get(track, 0) + 1
        return self._memory[track]

    def reset(self) -> None:
        self._memory.clear()


class RolloutRouter:
    def __init__(self, counter: Optional[RolloutSessionCounter] = None) -> None:
        self._counter = counter or RolloutSessionCounter()

    async def decide(self, *, session_id: str, track: str) -> RolloutDecision:
        from app.config.settings import settings

        if not settings.rollout_enabled:
            return RolloutDecision(
                allowed=True,
                track=track,
                stage=RolloutStage.OFF.value,
                reason="rollout_disabled",
            )

        if not session_id:
            return RolloutDecision(
                allowed=True,
                track=track,
                stage=RolloutStage.RAMP.value,
                reason="missing_session_id",
            )

        if track == RolloutTrack.FULL.value:
            return self._decide_full(session_id)
        if track == RolloutTrack.COMPACT.value:
            return await self._decide_compact(session_id)

        logger.warning("rollout_unknown_track", track=track)
        return RolloutDecision(
            allowed=True,
            track=track,
            stage=RolloutStage.COMPLETE.value,
            reason="unknown_track_fail_open",
        )

    def _decide_full(self, session_id: str) -> RolloutDecision:
        """full 档位：按百分比采样放量。"""
        from app.config.settings import settings

        percent = settings.rollout_full_percent
        bucket = bucket_of(session_id)
        allowed = bucket < percent
        return RolloutDecision(
            allowed=allowed,
            track=RolloutTrack.FULL.value,
            stage=RolloutStage.RAMP.value if allowed else RolloutStage.OFF.value,
            reason="percent_bucket_hit" if allowed else "percent_bucket_miss",
            bucket=bucket,
            percent=percent,
        )

    async def _decide_compact(self, session_id: str) -> RolloutDecision:
        """compact 档位：按会话数爬坡——首个会话 → N 个会话 → 全量。"""
        from app.config.settings import settings

        threshold = settings.rollout_compact_session_threshold
        session_index = await self._counter.next_index(RolloutTrack.COMPACT.value)

        if session_index <= 1:
            stage = RolloutStage.CANARY
        elif session_index <= threshold:
            stage = RolloutStage.RAMP
        else:
            stage = RolloutStage.COMPLETE

        return RolloutDecision(
            allowed=True,
            track=RolloutTrack.COMPACT.value,
            stage=stage.value,
            reason=f"compact_{stage.value}",
            session_index=session_index,
            percent=settings.rollout_full_percent,
        )


_router: Optional[RolloutRouter] = None


def get_rollout_router() -> RolloutRouter:
    global _router
    if _router is None:
        _router = RolloutRouter()
    return _router


def set_rollout_router(router: Optional[RolloutRouter]) -> None:
    """替换全局路由器（测试注入用）。"""
    global _router
    _router = router
