"""Safety Gateway Redis 订阅模块

支持两种消息模式：
- Pub/Sub：用于非关键消息（如脱敏规则同步通知）
- Streams：用于关键消息（如同意撤回、阈值更新），确保离线订阅者不丢失消息

设计文档04-安全合规要求同意撤回通知不可丢失，因此关键消息使用Redis Streams
而非Pub/Sub（Pub/Sub不持久化，离线订阅者会丢失消息）。
"""

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from safety_gateway.redis_pool import SafetyGatewayRedisPool

logger = structlog.get_logger()


class RedisSubscriber:
    """通用Redis Pub/Sub订阅框架（非关键消息）"""

    def __init__(
        self,
        channel: str,
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        self._channel = channel
        self._handler = handler
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._subscribe_loop())
        logger.info("sg_redis_subscriber_started", channel=self._channel)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:  # silent-ok: 停机主动 cancel 属正常收尾
                pass
            self._task = None
            logger.info("sg_redis_subscriber_stopped", channel=self._channel)

    async def _subscribe_loop(self) -> None:
        base_delay = 1.0
        max_delay = 30.0
        delay = base_delay
        while True:
            try:
                redis = await SafetyGatewayRedisPool.get_client()
                async with redis.pubsub() as pubsub:
                    await pubsub.subscribe(self._channel)
                    logger.info("sg_redis_pubsub_subscribed", channel=self._channel)
                    delay = base_delay
                    # ⚠️ 不能用 `async for message in pubsub.listen()`：它等价于
                    # `get_message(timeout=None)` —— **无限阻塞读**，必然撞上连接池的
                    # socket_timeout，真机每 ~9s 一条
                    #   sg_redis_pubsub_error channel=desensitization_rules_update
                    #   error=Timeout reading from 127.0.0.1:16379
                    # 这类"读超时"会被判成依赖不可用 → SG 回 E4005B（核心数据服务不可用）
                    # → 主服务连续失败进入快速失败（`safety_gateway_fast_fail_threshold_reached`）。
                    # 改为**有界轮询**：单次等待严格小于 socket 读超时（同一事实源推导），
                    # 空闲时循环取到 None 即可（与 RedisStreamConsumer 的处置同一思路）。
                    from safety_gateway.redis_pool import effective_socket_timeout

                    poll_timeout = max(1.0, effective_socket_timeout() - 3.0)
                    while True:
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=poll_timeout
                        )
                        if message is None:
                            continue
                        try:
                            data = json.loads(message["data"])
                            await self._handler(data)
                        except Exception as e:
                            logger.error(
                                "sg_redis_pubsub_handler_error",
                                channel=self._channel,
                                error=str(e),
                            )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "sg_redis_pubsub_error",
                    channel=self._channel,
                    error=str(e),
                    retry_in=delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)


class RedisStreamConsumer:
    """Redis Streams消费者（关键消息，不丢失）

    使用XREADGROUP实现消费者组模式，确保：
    - 消息持久化，离线期间的消息在重新连接后可消费
    - 消费者组模式下每条消息仅被一个消费者处理
    - XPENDING + XCLAIM实现未确认消息的重新分配
    """

    def __init__(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._handler = handler
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        await self._ensure_group()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info(
            "sg_redis_stream_consumer_started",
            stream=self._stream,
            group=self._group,
            consumer=self._consumer,
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:  # silent-ok: 停机主动 cancel 属正常收尾
                pass
            self._task = None
            logger.info("sg_redis_stream_consumer_stopped", stream=self._stream)

    async def _ensure_group(self) -> None:
        redis = await SafetyGatewayRedisPool.get_client()
        try:
            await redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
            logger.info(
                "sg_redis_stream_group_created",
                stream=self._stream,
                group=self._group,
            )
        except Exception as e:
            if "BUSYGROUP" in str(e):
                logger.debug(
                    "sg_redis_stream_group_exists",
                    stream=self._stream,
                    group=self._group,
                )
            else:
                logger.error(
                    "sg_redis_stream_group_create_error",
                    stream=self._stream,
                    group=self._group,
                    error=str(e),
                )

    async def _claim_pending_messages(self, redis: Any) -> None:
        try:
            pending = await redis.xpending_range(
                self._stream, self._group, min="-", max="+", count=10
            )
            if not pending:
                return

            stale_ids = []
            for entry in pending:
                consumer_name = entry.get("consumer_name", b"").decode() if isinstance(entry.get("consumer_name", b""), bytes) else str(entry.get("consumer_name", ""))
                idle_time_ms = entry.get("time_since_delivered", 0)
                if consumer_name != self._consumer and idle_time_ms > 30000:
                    msg_id = entry.get("message_id", "")
                    if isinstance(msg_id, bytes):
                        msg_id = msg_id.decode()
                    stale_ids.append(msg_id)

            if stale_ids:
                claimed = await redis.xclaim(
                    self._stream, self._group, self._consumer,
                    min_idle_time_ms=30000, message_ids=stale_ids
                )
                for msg_id, fields in claimed:
                    try:
                        data = {
                            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                            for k, v in fields.items()
                        }
                        payload_data = data.get("payload", "")
                        if payload_data:
                            try:
                                payload = json.loads(payload_data)
                                await self._handler(payload)
                            except json.JSONDecodeError:  # silent-ok: payload 非 JSON 时按原始字段字典交付
                                await self._handler(data)
                        else:
                            await self._handler(data)
                        await redis.xack(self._stream, self._group, msg_id)
                        logger.info("sg_redis_stream_claimed_message", stream=self._stream, msg_id=str(msg_id))
                    except Exception as e:
                        logger.error("sg_redis_stream_claim_handler_error", stream=self._stream, msg_id=str(msg_id), error=str(e))
        except Exception as e:
            logger.debug("sg_redis_stream_claim_check_error", stream=self._stream, error=str(e))

    async def _consume_loop(self) -> None:
        base_delay = 1.0
        max_delay = 30.0
        delay = base_delay
        while True:
            try:
                redis = await SafetyGatewayRedisPool.get_client()
                delay = base_delay

                await self._claim_pending_messages(redis)

                messages = await redis.xreadgroup(
                    self._group,
                    self._consumer,
                    {self._stream: ">"},
                    count=10,
                    block=5000,
                )
                if messages:
                    for stream_name, stream_messages in messages:
                        for msg_id, fields in stream_messages:
                            try:
                                data = {
                                    k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                                    for k, v in fields.items()
                                }
                                payload_data = data.get("payload", "")
                                if payload_data:
                                    try:
                                        payload = json.loads(payload_data)
                                        await self._handler(payload)
                                    except json.JSONDecodeError:  # silent-ok: payload 非 JSON 时按原始字段字典交付
                                        await self._handler(data)
                                else:
                                    action = data.get("action", "")
                                    if action:
                                        try:
                                            payload = json.loads(action)
                                            await self._handler(payload)
                                        except json.JSONDecodeError:  # silent-ok: action 非 JSON 时按原始字段字典交付
                                            await self._handler(data)
                                    else:
                                        await self._handler(data)
                                await redis.xack(self._stream, self._group, msg_id)
                            except Exception as e:
                                logger.error(
                                    "sg_redis_stream_handler_error",
                                    stream=self._stream,
                                    msg_id=str(msg_id),
                                    error=str(e),
                                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "sg_redis_stream_consume_error",
                    stream=self._stream,
                    error=str(e),
                    retry_in=delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
