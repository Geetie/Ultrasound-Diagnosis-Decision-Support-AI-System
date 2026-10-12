"""诊断准入控制（第二梯队② 生产容量验收，2026-10-12）。

**为什么需要它**：worker 池只有 ``int_worker_pool_size`` 个并发位（.env = 16），
超出的请求只能在 Redis 队列里排队。此前**没有任何准入判断**：队列可以涨到
``int_pending_queue_max_length``（1000）才拒绝，而压测实测 50 并发时结果接口
**集体超时**（端到端等待被算进 ``RESULT_TIMEOUT_SECONDS``）——医生既看不到进度、
最终也等不到结果，而排队中的请求还互相拖慢，把**已入队者**的 P99 一起拉爆。

**做法（早拒胜于久等）**：入队前看一眼队列深度，若已深到「等下去大概率超时」，
立刻返回 503 + ``Retry-After``（前端可明确重试），而不是让医生干等几分钟。
阈值与重试间隔都是**按压测容量标定**的参数（见 settings 注释与 16 号 A.53）。

**不做的事**：
- 不在此处做「用户级」限流（那是 ``check_rate_limit`` 的职责）；
- 不在 Redis 不可用时阻断请求 —— 那条路径由 ``enqueue_diagnosis`` 的
  in-process fallback 队列自己兜长度上限，这里探测不到深度就按 0 处理（放行）。
"""

from __future__ import annotations

import math

import structlog

from app.api.v1.errors import SystemQueueFullException
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.config.settings import settings

logger = structlog.get_logger()


async def current_pending_depth() -> int | None:
    """当前待处理队列深度（Redis pending list 长度）。

    Returns:
        int  —— 队列深度（Redis 不可用时退回 in-process fallback 队列长度；
                两者都拿不到时返回 ``None``，调用方按"探测不到 → 放行"处理）
    """
    try:
        client = await RedisPool.get_client()
        return int(await client.llen(RedisKey.DIAGNOSIS_PENDING_QUEUE))
    except Exception as exc:
        logger.warning(
            "admission_depth_probe_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # Redis 不可用：看进程内降级队列（与 enqueue_diagnosis 的 fallback 同源）
    try:
        from app.services.diagnosis_worker import get_pending_depth_sync

        return get_pending_depth_sync()
    except Exception:
        return None


def admission_wait_seconds(depth: int) -> int:
    """按队列深度估算需要等待的秒数（用于 Retry-After）。

    估算模型：``ceil(depth / worker_pool_size) × 单例服务时长``。单例时长取
    ``diagnosis_admission_assumed_service_s``（实测 e2e ≈ 8s 量级，见 16 号 A.53），
    结果钳在 [floor, cap]，避免给出荒谬的 Retry-After。
    """
    workers = max(int(settings.int_worker_pool_size or 1), 1)
    rounds = math.ceil(depth / workers)
    est = rounds * float(settings.diagnosis_admission_assumed_service_s)
    floor = float(settings.diagnosis_admission_retry_after_s)
    return int(min(max(est, floor), float(settings.diagnosis_admission_retry_after_cap_s)))


async def check_diagnosis_admission() -> int | None:
    """准入检查：队列过深则抛 :class:`SystemQueueFullException`（503 + Retry-After）。

    Returns:
        探测到的队列深度（放行时返回，便于调用方记日志/写 timings）；
        ``None`` 表示探测不到（已放行）。
    """
    if not settings.diagnosis_admission_enabled:
        return None

    depth = await current_pending_depth()
    if depth is None:
        return None

    limit = int(settings.diagnosis_admission_queue_depth_limit)
    if depth >= limit:
        wait_s = admission_wait_seconds(depth)
        logger.warning(
            "diagnosis_admission_rejected",
            queue_depth=depth,
            limit=limit,
            retry_after_s=wait_s,
        )
        raise SystemQueueFullException(
            error_message=(
                f"系统繁忙：当前有 {depth} 例判读在排队（上限 {limit}），"
                f"请约 {wait_s} 秒后重试 —— 现在提交也会排队更久，不如稍后再发。"
            ),
            headers={"Retry-After": str(wait_s)},
        )

    logger.debug("diagnosis_admission_passed", queue_depth=depth, limit=limit)
    return depth
