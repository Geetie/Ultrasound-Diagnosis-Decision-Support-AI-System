"""Safety Gateway阈值校验引擎模块

负责管理和校验诊断结论的置信度阈值，支持动态配置和热更新。

核心功能：
- 默认阈值配置（甲状腺、肝脏、心脏等器官）
- 从PostgreSQL动态加载病种级阈值
- 阈值缓存与自动刷新

拆分说明（FIX-P1-01）：
- 持久化逻辑 → threshold_persistence.py（ThresholdPersistence）
- 订阅逻辑 → threshold_subscriber.py（ThresholdSubscriber，使用Redis Streams）
- 本模块保留核心引擎：加载、校验、更新、统计

数据模型：
- OrganThreshold: 器官阈值定义（支持病种级别）
- ThresholdCheckResult: 阈值校验结果
"""

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, ClassVar

from sqlalchemy import select
import structlog
from pydantic import BaseModel

from safety_gateway.sg_db import get_db_session
from safety_gateway.sg_config import sg_settings as settings
from safety_gateway.retry import retry_with_backoff
from safety_gateway.sg_models import OrganDiseaseThreshold
from safety_gateway.validation_result import ThresholdCheckResult
from safety_gateway.threshold_persistence import ThresholdPersistence
from safety_gateway.threshold_subscriber import (
    ThresholdSubscriber,
    REDIS_CHANNEL_THRESHOLD_UPDATE,
)

logger = structlog.get_logger()


class OrganThreshold(BaseModel):
    organ: str
    confidence_threshold: float
    disease_type: str | None = None
    description: str = ""
    source: str = "default"


class ThresholdEngine:

    DEFAULT_THRESHOLDS: ClassVar[dict[str, float]] = {
        "thyroid": 0.85,
        "liver": 0.88,
        "cardiac": 0.92,
    }

    def __init__(self):
        self._thresholds: dict[str, OrganThreshold] = {}
        self._disease_thresholds: dict[str, dict[str, OrganThreshold]] = {}
        self._loaded = False
        self._last_load_time: datetime | None = None
        self._load_event = asyncio.Event()
        self._load_event.set()
        self._thresholds_lock = threading.Lock()
        self._subscriber: ThresholdSubscriber | None = None

    def _load_default_thresholds(self) -> None:
        for organ, threshold in self.DEFAULT_THRESHOLDS.items():
            self._thresholds[organ] = OrganThreshold(
                organ=organ,
                confidence_threshold=threshold,
                description=f"{organ}默认置信度阈值",
                source="default",
            )

    async def _load_thresholds_from_db_once(self) -> None:
        async with get_db_session() as session:
            result = await session.execute(
                select(OrganDiseaseThreshold)
            )
            db_thresholds = result.scalars().all()

            with self._thresholds_lock:
                for threshold in db_thresholds:
                    key = threshold.organ
                    if threshold.disease_type:
                        if key not in self._disease_thresholds:
                            self._disease_thresholds[key] = {}
                        self._disease_thresholds[key][threshold.disease_type] = OrganThreshold(
                            organ=threshold.organ,
                            confidence_threshold=threshold.confidence_threshold,
                            disease_type=threshold.disease_type,
                            description=threshold.description,
                            source="database",
                        )
                    else:
                        self._thresholds[key] = OrganThreshold(
                            organ=threshold.organ,
                            confidence_threshold=threshold.confidence_threshold,
                            description=threshold.description,
                            source="database",
                        )

                self._loaded = True
                self._last_load_time = datetime.now(timezone.utc)
                logger.info(
                    "sg_thresholds_loaded_from_db",
                    count=len(db_thresholds),
                    organs=list(self._thresholds.keys()),
                )

    async def load_thresholds_from_db(self) -> None:
        if self._loaded:
            return

        if not self._load_event.is_set():
            await self._load_event.wait()
            return

        self._load_event.clear()
        try:
            await retry_with_backoff(
                self._load_thresholds_from_db_once,
                max_attempts=settings.sg_db_retry_max_attempts,
                delay_base=settings.sg_db_retry_delay_base,
                retryable_exceptions=(Exception,),
                log_prefix="sg_thresholds_load",
            )
        except Exception:
            logger.error("sg_thresholds_load_failed_after_retries")
            raise
        finally:
            self._load_event.set()

    def _load_sync_fallback(self, db_thresholds: dict[str, float] | None = None) -> None:
        if not self._load_event.is_set():
            logger.warning("sg_thresholds_sync_load_skipped_async_in_progress")
            return

        with self._thresholds_lock:
            self._load_default_thresholds()

            if db_thresholds:
                for organ, threshold in db_thresholds.items():
                    self._thresholds[organ] = OrganThreshold(
                        organ=organ,
                        confidence_threshold=threshold,
                        description=f"{organ}数据库配置阈值",
                        source="manual",
                    )

            self._loaded = True
            self._last_load_time = datetime.now(timezone.utc)
        logger.info(
            "sg_thresholds_loaded",
            thresholds={o: t.confidence_threshold for o, t in self._thresholds.items()},
        )

    async def ensure_loaded(self) -> None:
        if not self._loaded or self._needs_refresh():
            await self._lazy_load()

    def _needs_refresh(self) -> bool:
        if self._last_load_time is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_load_time).total_seconds()
        return elapsed >= settings.sg_threshold_cache_ttl_seconds

    async def _lazy_load(self) -> None:
        try:
            await self.load_thresholds_from_db()
        except Exception as e:
            logger.warning(
                "sg_thresholds_fallback_to_default",
                error=str(e),
            )
            if not self._loaded:
                self._load_default_thresholds()
                self._loaded = True
                self._last_load_time = datetime.now(timezone.utc)
                logger.info("sg_thresholds_loaded_defaults_after_db_failure")

    def _check_sync(
        self,
        organ: str,
        confidence: float,
        disease_type: str | None = None,
    ) -> ThresholdCheckResult:
        threshold = self._get_threshold(organ, disease_type)
        passed = confidence >= threshold

        if not passed:
            logger.warning(
                "sg_confidence_below_threshold",
                organ=organ,
                disease_type=disease_type,
                confidence=confidence,
                threshold=threshold,
            )

        return ThresholdCheckResult(
            organ=organ,
            confidence=confidence,
            threshold=threshold,
            passed=passed,
            message="置信度达标" if passed else f"置信度{confidence}低于阈值{threshold}",
        )

    async def check(
        self,
        organ: str,
        confidence: float,
        disease_type: str | None = None,
    ) -> ThresholdCheckResult:
        await self.ensure_loaded()
        return self._check_sync(organ, confidence, disease_type)

    async def check_multiple(
        self,
        conclusions: list[dict[str, Any]],
    ) -> list[ThresholdCheckResult]:
        await self.ensure_loaded()
        return self._check_multiple_sync(conclusions)

    def _check_multiple_sync(
        self,
        conclusions: list[dict[str, Any]],
    ) -> list[ThresholdCheckResult]:
        results = []
        for conclusion in conclusions:
            organ = conclusion.get("organ", "")
            confidence = conclusion.get("confidence", 0.0)
            disease_type = conclusion.get("disease_type", None)
            if organ and confidence:
                results.append(self._check_sync(organ, confidence, disease_type))
        return results

    def _get_threshold(self, organ: str, disease_type: str | None = None) -> float:
        if disease_type and organ in self._disease_thresholds and disease_type in self._disease_thresholds[organ]:
            return self._disease_thresholds[organ][disease_type].confidence_threshold

        if organ in self._thresholds:
            return self._thresholds[organ].confidence_threshold

        logger.warning("sg_unknown_organ_threshold", organ=organ, disease_type=disease_type)
        return settings.sg_default_unknown_organ_threshold

    async def get_all_thresholds(self) -> dict[str, OrganThreshold]:
        if not self._loaded:
            await self.load_thresholds_from_db()
        return self._thresholds

    async def update_threshold(self, organ: str, threshold: float, disease_type: str | None = None) -> None:
        if not self._loaded:
            await self.load_thresholds_from_db()

        self._update_threshold_sync(organ, threshold, disease_type)
        await ThresholdPersistence.persist_to_db(organ, threshold, disease_type)
        logger.info("sg_threshold_updated", organ=organ, disease_type=disease_type, new_threshold=threshold, persisted=True)

    def _update_threshold_sync(self, organ: str, threshold: float, disease_type: str | None = None) -> None:
        if disease_type:
            if organ not in self._disease_thresholds:
                self._disease_thresholds[organ] = {}
            self._disease_thresholds[organ][disease_type] = OrganThreshold(
                organ=organ,
                confidence_threshold=threshold,
                disease_type=disease_type,
                description=f"{organ}-{disease_type}动态更新阈值",
                source="api",
            )
        else:
            self._thresholds[organ] = OrganThreshold(
                organ=organ,
                confidence_threshold=threshold,
                description=f"{organ}动态更新阈值",
                source="api",
            )

    def _delete_threshold_sync(self, organ: str, disease_type: str | None = None) -> bool:
        with self._thresholds_lock:
            if disease_type and organ in self._disease_thresholds and disease_type in self._disease_thresholds[organ]:
                del self._disease_thresholds[organ][disease_type]
                return True
            elif organ and organ in self._thresholds:
                del self._thresholds[organ]
                if organ in self._disease_thresholds:
                    del self._disease_thresholds[organ]
                return True
        return False

    async def refresh(self) -> None:
        self._loaded = False
        self._last_load_time = None
        await self.load_thresholds_from_db()

    def get_stats(self) -> dict[str, Any]:
        return {
            "loaded": self._loaded,
            "threshold_count": len(self._thresholds),
            "disease_threshold_count": sum(
                len(diseases) for diseases in self._disease_thresholds.values()
            ),
            "last_load_time": self._last_load_time.isoformat() if self._last_load_time else None,
            "needs_refresh": self._needs_refresh(),
            "subscribe_running": self._subscriber is not None,
        }

    async def start_subscribe(self) -> None:
        if self._subscriber is not None:
            logger.warning("sg_threshold_subscribe_already_running")
            return

        self._subscriber = ThresholdSubscriber(engine=self)
        await self._subscriber.start()

    async def stop_subscribe(self) -> None:
        if self._subscriber:
            await self._subscriber.stop()
            self._subscriber = None

    async def publish_update(
        self,
        action: str,
        organ: str | None = None,
        threshold: float | None = None,
        disease_type: str | None = None,
    ) -> None:
        if self._subscriber:
            await self._subscriber.publish_update(action, organ, threshold, disease_type)
        else:
            subscriber = ThresholdSubscriber(engine=self)
            await subscriber.publish_update(action, organ, threshold, disease_type)


from safety_gateway.registry import sg_registry

_sg_threshold_lock = threading.Lock()


def get_threshold_engine() -> ThresholdEngine:
    if not sg_registry.is_registered("threshold_engine"):
        with _sg_threshold_lock:
            if not sg_registry.is_registered("threshold_engine"):
                sg_registry.register("threshold_engine", ThresholdEngine)
    return sg_registry.get("threshold_engine")


def _reset_threshold_engine() -> None:
    sg_registry.reset("threshold_engine")


async def start_threshold_subscribe() -> None:
    engine = get_threshold_engine()
    await engine.start_subscribe()


async def stop_threshold_subscribe() -> None:
    engine = get_threshold_engine()
    await engine.stop_subscribe()
