import asyncio
import json
from typing import Any

import structlog

from safety_gateway.redis_pool import SafetyGatewayRedisPool
from safety_gateway.redis_subscriber import RedisStreamConsumer
from safety_gateway.threshold_persistence import ThresholdPersistence

logger = structlog.get_logger()

REDIS_CHANNEL_THRESHOLD_UPDATE = "sg:threshold:update"
REDIS_STREAM_THRESHOLD_UPDATE = "sg:threshold:stream"
REDIS_STREAM_GROUP = "sg-threshold-group"
REDIS_STREAM_CONSUMER = "sg-worker-1"


class ThresholdSubscriber:
    """阈值更新订阅组件

    使用Redis Streams（而非Pub/Sub）消费阈值更新消息，
    确保离线期间的消息不丢失（FIX-P2-02）。

    从ThresholdEngine中拆分出来，降低单文件复杂度。
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._consumer: RedisStreamConsumer | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            logger.warning("sg_threshold_subscribe_already_running")
            return

        self._running = True
        self._consumer = RedisStreamConsumer(
            stream=REDIS_STREAM_THRESHOLD_UPDATE,
            group=REDIS_STREAM_GROUP,
            consumer=REDIS_STREAM_CONSUMER,
            handler=self._handle_threshold_update,
        )
        await self._consumer.start()
        logger.info("sg_threshold_subscribe_started", mode="stream")

    async def stop(self) -> None:
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None
        logger.info("sg_threshold_subscribe_stopped")

    async def _handle_threshold_update(self, data: dict[str, Any]) -> None:
        try:
            action = data.get("action")

            if action == "refresh":
                await self._engine.refresh()
                logger.info("sg_threshold_refreshed_via_subscribe")

            elif action == "update":
                organ = data.get("organ")
                threshold = data.get("threshold")
                disease_type = data.get("disease_type")

                if organ and threshold is not None:
                    self._engine._update_threshold_sync(organ, threshold, disease_type)
                    try:
                        await ThresholdPersistence.persist_to_db(organ, threshold, disease_type)
                    except Exception as e:
                        logger.error("sg_threshold_persist_failed", organ=organ, disease_type=disease_type, error=str(e))
                    logger.info(
                        "sg_threshold_updated_via_subscribe",
                        organ=organ,
                        disease_type=disease_type,
                        threshold=threshold,
                    )

            elif action == "delete":
                organ = data.get("organ")
                disease_type = data.get("disease_type")

                deleted = self._engine._delete_threshold_sync(organ, disease_type)
                if deleted:
                    try:
                        await ThresholdPersistence.delete_from_db(organ, disease_type)
                    except Exception as e:
                        logger.error("sg_threshold_delete_persist_failed", organ=organ, disease_type=disease_type, error=str(e))
                    logger.info("sg_threshold_deleted_via_subscribe", organ=organ, disease_type=disease_type)

        except Exception as e:
            logger.error("sg_threshold_subscribe_handle_error", error=str(e))

    async def publish_update(
        self,
        action: str,
        organ: str | None = None,
        threshold: float | None = None,
        disease_type: str | None = None,
    ) -> None:
        message = {"action": action}
        if organ:
            message["organ"] = organ
        if threshold is not None:
            message["threshold"] = threshold
        if disease_type:
            message["disease_type"] = disease_type

        try:
            redis = await SafetyGatewayRedisPool.get_client()
            await redis.xadd(REDIS_STREAM_THRESHOLD_UPDATE, {"payload": json.dumps(message)})
            logger.info("sg_threshold_update_published", message=message, mode="stream")
        except Exception as e:
            logger.error("sg_threshold_publish_failed", error=str(e))
