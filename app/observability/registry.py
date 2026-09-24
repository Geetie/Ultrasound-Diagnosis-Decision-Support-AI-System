"""OBS组件注册表 — 统一管理可观测性组件的单例生命周期。

替代各模块独立的双重检查锁单例实现，提供统一的注册、获取、重置和覆盖接口，
支持线程安全和测试时的依赖注入。
"""

__all__ = ["OBSComponentRegistry", "get_obs_registry"]

import threading
from collections.abc import Callable
from typing import Any

import structlog

_logger = structlog.get_logger("app.observability.registry")


class OBSComponentRegistry:
    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        self._lock = threading.Lock()

    def register(self, name: str, factory: Callable[[], Any]) -> None:
        with self._lock:
            self._factories[name] = factory
            if name in self._instances:
                del self._instances[name]

    def get(self, name: str) -> Any:
        if name not in self._instances:
            with self._lock:
                if name not in self._instances:
                    if name not in self._factories:
                        raise KeyError(f"Component '{name}' not registered in OBSComponentRegistry")
                    self._instances[name] = self._factories[name]()
        return self._instances[name]

    def reset(self, name: str | None = None) -> None:
        with self._lock:
            if name is None:
                self._instances.clear()
            elif name in self._instances:
                del self._instances[name]

    def override(self, name: str, instance: Any) -> None:
        with self._lock:
            self._instances[name] = instance

    def is_registered(self, name: str) -> bool:
        return name in self._factories

    def has_instance(self, name: str) -> bool:
        return name in self._instances


_registry: OBSComponentRegistry | None = None
_registry_lock = threading.Lock()


def get_obs_registry() -> OBSComponentRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = OBSComponentRegistry()
                _register_defaults(_registry)
    return _registry


def _register_defaults(registry: OBSComponentRegistry) -> None:
    from app.observability.alert_escalation import AlertEscalationManager
    from app.observability.cache_monitor import CacheMonitor
    from app.observability.cusum import AISafetyCircuitBreaker, HallucinationCUSUMBridge
    from app.observability.logging import LogStorage
    from app.observability.metrics_collector import MetricsCollector

    registry.register("metrics_collector", lambda: MetricsCollector.get_instance())
    registry.register("cache_monitor", lambda: CacheMonitor.get_instance())
    registry.register("log_storage", lambda: LogStorage.get_instance())
    registry.register("alert_escalation_manager", lambda: AlertEscalationManager())
    registry.register("hallucination_cusum_bridge", lambda: HallucinationCUSUMBridge.get_instance())
    registry.register("circuit_breaker", lambda: AISafetyCircuitBreaker.get_instance())


def _reset_obs_registry() -> None:
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.reset()
        _registry = None
