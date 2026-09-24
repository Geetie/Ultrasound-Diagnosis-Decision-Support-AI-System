"""T-GB-LLM-01 — 模型热重载编排。

规格（``01-系统架构设计.md`` / ``05-可观测性与运维.md``）：

- 显存碎片化 > 30% → Warning；
- > 40% → 触发模型热重载；
- **无活跃会话立即执行**；
- **有活跃会话延迟至会话结束，最长 10 分钟**。

设计要点：

1. **只做编排，不做权重加载**：真实的模型重载（vLLM / llama.cpp 权重热
   加载）由注入的 :class:`ModelReloadAdapter` 承担。本地用
   :class:`LocalFakeReloadAdapter` 验证「阈值触发 → 会话检测 → 立即/延迟
   执行 → 指标写入」全链路，现场只需 :func:`set_reload_adapter` 注入真实
   实现——与 EDG / TensorRT 的既有处理方式一致。
2. **阈值不重复定义**：直接复用 ``settings.gpu_fragmentation_warning_threshold``
   (0.30) 与 ``settings.gpu_fragmentation_critical_threshold`` (0.40)，
   避免与 GPU 监控模块出现两份数字。
3. **单飞（single-flight）**：同一时刻只允许一次重载在途，避免碎片化持续
   超阈值时反复触发把推理服务打满。
4. **延迟上限兜底**：等到 ``hot_reload_max_defer_seconds``（默认 600s）仍
   有活跃会话时**强制执行**并标记 ``forced=True``——碎片化不重载会持续
   恶化直至 OOM，比"再等一个会话"更危险。
5. **指标接线**：``set_model_hot_reload_success_rate`` /
   ``record_model_hot_reload_duration`` 此前无任何写入方，由本模块写入。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Protocol, runtime_checkable

import structlog

from app.config.settings import settings

logger = structlog.get_logger("app.inference.hot_reload")


# ----------------------------------------------------------------------
# 重载适配器（现场注入点）
# ----------------------------------------------------------------------


@runtime_checkable
class ModelReloadAdapter(Protocol):
    """真实模型重载的注入契约。"""

    async def reload(self, *, reason: str) -> bool:
        """执行一次权重/引擎重载，返回是否成功。"""
        ...

    async def health(self) -> bool:
        """适配器自身可用性（供就绪探针使用）。"""
        ...


class LocalFakeReloadAdapter:
    """本地假实现：不做真实加载，仅记录调用次数并总是成功。

    用于在无 GPU 环境下验证编排逻辑（阈值、会话检测、延迟执行、指标）。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.reasons: list[str] = []
        self.fail_next = False

    async def reload(self, *, reason: str) -> bool:
        self.calls += 1
        self.reasons.append(reason)
        if self.fail_next:
            self.fail_next = False
            return False
        return True

    async def health(self) -> bool:
        return True


_default_adapter: Optional[ModelReloadAdapter] = None


def set_reload_adapter(adapter: Optional[ModelReloadAdapter]) -> None:
    """注入（或清除）进程级重载适配器。现场对接时调用。"""
    global _default_adapter
    _default_adapter = adapter


def get_reload_adapter(settings_obj: Any = None) -> ModelReloadAdapter:
    """取得重载适配器：已注入的优先，否则按 ``settings.hot_reload_adapter`` 构造。

    Raises:
        ValueError: 配置为 ``local_fake`` 以外的名字，但没有注入真实适配器。
            这里选择**显式失败**而非静默降级，避免生产环境"看起来在重载"。
    """
    global _default_adapter
    if _default_adapter is not None:
        return _default_adapter
    name = str(getattr(settings_obj or settings, "hot_reload_adapter", "local_fake"))
    if name == "local_fake":
        _default_adapter = LocalFakeReloadAdapter()
        return _default_adapter
    raise ValueError(
        f"unknown hot_reload_adapter: {name!r}; "
        "call app.inference.hot_reload.set_reload_adapter() to inject a real one"
    )


# ----------------------------------------------------------------------
# 结果对象
# ----------------------------------------------------------------------


@dataclass
class HotReloadOutcome:
    """一次碎片化采样（或显式请求）的处理结果。"""

    triggered: bool = False
    executed: bool = False
    deferred: bool = False
    forced: bool = False
    success: Optional[bool] = None
    fragmentation_rate: float = 0.0
    trigger_threshold: float = 0.0
    active_sessions: int = 0
    waited_seconds: float = 0.0
    duration_seconds: float = 0.0
    reason: str = ""
    skipped_reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "triggered": self.triggered,
            "executed": self.executed,
            "deferred": self.deferred,
            "forced": self.forced,
            "success": self.success,
            "fragmentation_rate": round(self.fragmentation_rate, 4),
            "trigger_threshold": self.trigger_threshold,
            "active_sessions": self.active_sessions,
            "waited_seconds": round(self.waited_seconds, 2),
            "duration_seconds": round(self.duration_seconds, 2),
            "reason": self.reason,
            "skipped_reason": self.skipped_reason,
        }


@dataclass
class HotReloadStats:
    samples: int = 0
    warnings: int = 0
    triggered: int = 0
    executed: int = 0
    deferred: int = 0
    forced: int = 0
    failed: int = 0
    last_rate: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "samples": self.samples,
            "warnings": self.warnings,
            "triggered": self.triggered,
            "executed": self.executed,
            "deferred": self.deferred,
            "forced": self.forced,
            "failed": self.failed,
            "last_rate": round(self.last_rate, 4),
        }


# ----------------------------------------------------------------------
# 活跃会话检测
# ----------------------------------------------------------------------


async def _default_active_sessions_provider() -> int:
    """默认活跃会话数：WebSocket 导航连接数（无连接即视为空闲）。"""
    try:
        from app.api.websocket.nav import manager as nav_manager

        conns = getattr(nav_manager, "active_connections", None)
        return len(conns) if conns else 0
    except Exception as exc:  # noqa: BLE001 - 检测失败按"无活跃会话"从严处理
        logger.warning(
            "hot_reload_active_sessions_probe_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return 0


# ----------------------------------------------------------------------
# 编排器
# ----------------------------------------------------------------------


class HotReloadController:
    """把碎片化信号转为"立即或延迟"的模型重载动作。

    Args:
        adapter: 重载适配器；``None`` 时用 :func:`get_reload_adapter`。
        active_sessions_provider: 返回当前活跃会话数的 callable（可同步可异步）。
        warning_threshold: Warning 阈值（默认取 settings，0.30）。
        trigger_threshold: 触发阈值（默认取 settings，0.40）。
        max_defer_seconds: 有活跃会话时的最大延迟（默认 600s）。
        poll_interval_seconds: 等待会话结束的轮询间隔（默认 5s）。
        metrics_factory: 指标采集器工厂（测试注入用）。
        sleep: 休眠函数（测试注入用，默认 ``asyncio.sleep``）。
    """

    def __init__(
        self,
        adapter: Optional[ModelReloadAdapter] = None,
        active_sessions_provider: Optional[Callable[[], Any]] = None,
        *,
        warning_threshold: Optional[float] = None,
        trigger_threshold: Optional[float] = None,
        max_defer_seconds: Optional[float] = None,
        poll_interval_seconds: Optional[float] = None,
        metrics_factory: Optional[Callable[[], Any]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        warn = (
            warning_threshold
            if warning_threshold is not None
            else settings.gpu_fragmentation_warning_threshold
        )
        trigger = (
            trigger_threshold
            if trigger_threshold is not None
            else settings.gpu_fragmentation_critical_threshold
        )
        if trigger <= warn:
            raise ValueError(
                "trigger_threshold must be greater than warning_threshold "
                f"(got warning={warn}, trigger={trigger})"
            )

        self._adapter = adapter if adapter is not None else get_reload_adapter()
        self._active_sessions_provider = (
            active_sessions_provider or _default_active_sessions_provider
        )
        self._warning_threshold = float(warn)
        self._trigger_threshold = float(trigger)
        self._max_defer = float(
            max_defer_seconds
            if max_defer_seconds is not None
            else settings.hot_reload_max_defer_seconds
        )
        self._poll_interval = float(
            poll_interval_seconds
            if poll_interval_seconds is not None
            else settings.hot_reload_poll_interval_seconds
        )
        if self._max_defer < 0:
            raise ValueError("max_defer_seconds must be >= 0")
        if self._poll_interval <= 0:
            raise ValueError("poll_interval_seconds must be > 0")

        self._metrics_factory = metrics_factory
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._stats = HotReloadStats()

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    @property
    def warning_threshold(self) -> float:
        return self._warning_threshold

    @property
    def trigger_threshold(self) -> float:
        return self._trigger_threshold

    def stats(self) -> HotReloadStats:
        return self._stats

    async def on_fragmentation_sample(
        self, fragmentation_rate: float
    ) -> HotReloadOutcome:
        """处理一次碎片化采样。

        - ``<= warning``：无事发生（仅计数）；
        - ``> warning`` 且 ``<= trigger``：记 Warning；
        - ``> trigger``：进入"立即或延迟"重载流程。
        """
        self._stats.samples += 1
        self._stats.last_rate = float(fragmentation_rate)

        if fragmentation_rate <= self._warning_threshold:
            return HotReloadOutcome(
                fragmentation_rate=float(fragmentation_rate),
                trigger_threshold=self._trigger_threshold,
            )

        if fragmentation_rate <= self._trigger_threshold:
            self._stats.warnings += 1
            logger.warning(
                "gpu_fragmentation_above_warning",
                fragmentation_rate=round(float(fragmentation_rate), 4),
                warning_threshold=self._warning_threshold,
                trigger_threshold=self._trigger_threshold,
            )
            return HotReloadOutcome(
                fragmentation_rate=float(fragmentation_rate),
                trigger_threshold=self._trigger_threshold,
            )

        self._stats.triggered += 1
        logger.warning(
            "model_hot_reload_triggered",
            fragmentation_rate=round(float(fragmentation_rate), 4),
            trigger_threshold=self._trigger_threshold,
        )
        return await self._execute_or_defer(
            fragmentation_rate=float(fragmentation_rate),
            reason=f"fragmentation:{fragmentation_rate:.4f}",
        )

    async def request_reload(self, reason: str) -> HotReloadOutcome:
        """显式请求一次重载（运维手动触发 / 阈值之外的场景）。"""
        self._stats.triggered += 1
        return await self._execute_or_defer(fragmentation_rate=0.0, reason=reason)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _execute_or_defer(
        self, *, fragmentation_rate: float, reason: str
    ) -> HotReloadOutcome:
        """无活跃会话立即执行；有活跃会话延迟至空闲或超时强制。"""
        if self._lock.locked():
            logger.info("model_hot_reload_skipped_in_flight", reason=reason)
            return HotReloadOutcome(
                triggered=True,
                fragmentation_rate=fragmentation_rate,
                trigger_threshold=self._trigger_threshold,
                reason=reason,
                skipped_reason="reload_in_flight",
            )

        async with self._lock:
            outcome = HotReloadOutcome(
                triggered=True,
                fragmentation_rate=fragmentation_rate,
                trigger_threshold=self._trigger_threshold,
                reason=reason,
            )
            wait_started = time.monotonic()
            active = await self._active_sessions()
            outcome.active_sessions = active

            if active > 0:
                outcome.deferred = True
                self._stats.deferred += 1
                logger.info(
                    "model_hot_reload_deferred",
                    active_sessions=active,
                    max_defer_seconds=self._max_defer,
                    reason=reason,
                )
                while active > 0 and (time.monotonic() - wait_started) < self._max_defer:
                    await self._sleep(self._poll_interval)
                    active = await self._active_sessions()
                outcome.waited_seconds = time.monotonic() - wait_started
                outcome.active_sessions = active
                if active > 0:
                    # 延迟上限兜底：碎片化继续恶化比"再等一个会话"更危险
                    outcome.forced = True
                    self._stats.forced += 1
                    logger.warning(
                        "model_hot_reload_forced_after_max_defer",
                        active_sessions=active,
                        waited_seconds=round(outcome.waited_seconds, 2),
                        reason=reason,
                    )

            started = time.monotonic()
            try:
                success = bool(await self._adapter.reload(reason=reason))
            except Exception as exc:  # noqa: BLE001 - 重载失败不得抛出中断采样循环
                success = False
                logger.error(
                    "model_hot_reload_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    reason=reason,
                )
            outcome.duration_seconds = time.monotonic() - started
            outcome.executed = True
            outcome.success = success
            self._stats.executed += 1
            if not success:
                self._stats.failed += 1

            self._publish_metrics(outcome)
            logger.info("model_hot_reload_finished", **outcome.as_dict())
            return outcome

    async def _active_sessions(self) -> int:
        """读取活跃会话数（异常按 0 处理，保证重载不被探活故障卡住）。"""
        try:
            value = self._active_sessions_provider()
            if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
                value = await value
            return max(0, int(value))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hot_reload_active_sessions_provider_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return 0

    def _publish_metrics(self, outcome: HotReloadOutcome) -> None:
        """写入热重载成功率与耗时指标（此前无任何写入方）。"""
        try:
            if self._metrics_factory is not None:
                collector = self._metrics_factory()
            else:
                from app.observability.metrics_collector import get_metrics_collector

                collector = get_metrics_collector()
            collector.set_model_hot_reload_success_rate(
                1.0 if outcome.success else 0.0
            )
            collector.record_model_hot_reload_duration(outcome.duration_seconds)
        except Exception as exc:  # noqa: BLE001 - 埋点失败不得影响业务
            logger.debug("model_hot_reload_metric_failed", error=str(exc))


# ----------------------------------------------------------------------
# 进程级单例
# ----------------------------------------------------------------------

_controller: Optional[HotReloadController] = None


def get_hot_reload_controller() -> HotReloadController:
    """取得进程级控制器单例（GPU 监控模块共享）。"""
    global _controller
    if _controller is None:
        _controller = HotReloadController()
    return _controller


def reset_hot_reload_controller() -> None:
    """重置单例（测试与 reload 场景用）。"""
    global _controller
    _controller = None


__all__ = [
    "HotReloadController",
    "HotReloadOutcome",
    "HotReloadStats",
    "LocalFakeReloadAdapter",
    "ModelReloadAdapter",
    "get_hot_reload_controller",
    "get_reload_adapter",
    "reset_hot_reload_controller",
    "set_reload_adapter",
]
