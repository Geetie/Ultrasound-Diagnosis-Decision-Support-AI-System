from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from safety_gateway.sg_config import sg_settings as settings
from safety_gateway.health.latency_tracker import LatencyTracker
from safety_gateway.health.memory_monitor import MemoryMonitor
from safety_gateway.health.fusion_probe import FusionProbe
from safety_gateway.health import providers

logger = structlog.get_logger()


class HealthChecker:
    """SafetyGateway健康检查器（编排器）

    职责：聚合各组件健康检查结果，计算整体状态。

    子组件：
    - LatencyTracker: 延迟采样与百分位统计
    - MemoryMonitor: 跨平台内存使用监控
    - FusionProbe: 融合层可用性探测+TTL缓存
    - providers: 各组件健康检查函数

    主服务应通过以下规范实现SG运行时健康探测（不在SG包内实现，职责分离）：
    - 探测间隔：4秒
    - 探测超时：1秒
    - 连续3次失败判定SG不可用（3 x (4s + 1s) = 15s）
    - SG不可用时：所有诊断输出拒绝（E4005），系统进入纯人工模式
    - SG短暂不可用（<=15s）：融合层输出排队等待恢复，超时后拒绝输出
    """

    def __init__(self) -> None:
        self._start_time = datetime.now(timezone.utc)
        self._latency_tracker = LatencyTracker()
        self._memory_monitor = MemoryMonitor()
        self._fusion_probe = FusionProbe()

    async def aclose(self) -> None:
        await self._fusion_probe.aclose()

    async def check_all(self) -> dict[str, Any]:
        """聚合各组件健康检查结果，计算整体状态。

        设计决策：融合层(fusion_layer)状态不参与SG自身健康判定。
        理由：SG在融合层不可用时仍可独立提供降级输出服务，
        将融合层不可用标记为SG unhealthy会导致主服务错误地
        判定SG不可用并拒绝所有输出（E4005），而实际上SG仍可
        提供降级输出。融合层状态仅作为信息性组件展示，
        供调用方自行决定降级策略。
        """
        redis_status = await self._check_redis()
        db_status = await self._check_database()
        rules_status = self._check_rules_engine()
        circuit_breaker_status = self._check_circuit_breaker()
        threshold_status = self._check_threshold_engine()
        oom_status = self._check_oom_protection()
        fusion_status = await self._check_fusion_layer()

        components_healthy = (
            redis_status["status"] == "healthy"
            and db_status["status"] == "healthy"
            and rules_status["status"] == "healthy"
            and circuit_breaker_status["status"] == "healthy"
            and threshold_status["status"] == "healthy"
            and oom_status["status"] in ("protected", "configured")
        )

        all_component_statuses = [redis_status, db_status, rules_status, circuit_breaker_status, threshold_status]
        components_degraded = any(
            s["status"] == "degraded"
            for s in all_component_statuses
        ) or oom_status["status"] in ("warning", "unknown")

        if components_healthy:
            overall = "healthy"
        elif components_degraded and all(
            s["status"] in ("healthy", "degraded")
            for s in [redis_status, db_status, rules_status, circuit_breaker_status, threshold_status]
        ):
            overall = "degraded"
        else:
            overall = "unhealthy"

        if redis_status["status"] == "unhealthy" and settings.deployment_profile == "compact":
            other_unhealthy = any(
                s["status"] == "unhealthy"
                for name, s in [("database", db_status), ("rules_engine", rules_status), ("circuit_breaker", circuit_breaker_status), ("threshold_engine", threshold_status)]
            )
            if not other_unhealthy:
                overall = "degraded"
                redis_status["impact"] = "degraded"
            else:
                redis_status["impact"] = "unhealthy"

        uptime = datetime.now(timezone.utc) - self._start_time

        latency_stats = await self._latency_tracker.compute_stats()
        e2e_latency_stats = await self._latency_tracker.compute_e2e_stats()

        memory_mb = self._memory_monitor.get_memory_usage_mb()
        memory_info = self._memory_monitor.get_memory_status(memory_mb, settings.sg_memory_budget_mb)

        result = {
            "status": overall,
            "service": "safety-gateway",
            "version": "1.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "port": settings.safety_gateway_port,
            "uptime_seconds": int(uptime.total_seconds()),
            "uptime_human": str(uptime).split(".")[0],
            "latency_stats": latency_stats,
            "e2e_latency_stats": e2e_latency_stats,
            "memory": memory_info,
            "oom_protection": oom_status,
            "components": {
                "redis": redis_status,
                "database": db_status,
                "rules_engine": rules_status,
                "circuit_breaker": circuit_breaker_status,
                "threshold_engine": threshold_status,
                "fusion_layer": fusion_status,
            },
        }

        if overall == "unhealthy":
            logger.warning("sg_health_check_unhealthy", components=result["components"])

        return result

    async def compute_latency_stats(self) -> dict[str, Any]:
        return await self._latency_tracker.compute_stats()

    async def compute_e2e_latency_stats(self) -> dict[str, Any]:
        return await self._latency_tracker.compute_e2e_stats()

    async def record_latency(self, latency_ms: float) -> None:
        await self._latency_tracker.record(latency_ms)

    async def record_e2e_latency(self, latency_ms: float) -> None:
        await self._latency_tracker.record_e2e(latency_ms)

    async def get_fusion_availability(self) -> bool | None:
        return await self._fusion_probe.get_availability()

    def get_start_time(self) -> datetime:
        return self._start_time

    def get_uptime(self) -> timedelta:
        return datetime.now(timezone.utc) - self._start_time

    @staticmethod
    def _get_memory_usage_mb() -> float:
        return MemoryMonitor.get_memory_usage_mb()

    async def _check_redis(self) -> dict[str, Any]:
        return await providers.check_redis()

    async def _check_database(self) -> dict[str, Any]:
        return await providers.check_database()

    def _check_rules_engine(self) -> dict[str, Any]:
        return providers.check_rules_engine()

    def _check_circuit_breaker(self) -> dict[str, Any]:
        return providers.check_circuit_breaker()

    def _check_oom_protection(self) -> dict[str, Any]:
        return providers.check_oom_protection()

    def _check_threshold_engine(self) -> dict[str, Any]:
        return providers.check_threshold_engine()

    async def _check_fusion_layer(self) -> dict[str, Any]:
        return await self._fusion_probe.check()

    def _get_p99_status(self, p99_ms: float) -> str:
        return self._latency_tracker._get_p99_status(
            p99_ms,
            settings.sg_p99_latency_warning_threshold_ms,
            settings.sg_p99_latency_critical_threshold_ms,
        )

    def _percentile(self, data: list[float], p: int) -> float:
        return self._latency_tracker._percentile(data, p)
