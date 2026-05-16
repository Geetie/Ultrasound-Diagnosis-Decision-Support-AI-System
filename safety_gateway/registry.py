import threading
from typing import Any, Callable

import structlog

logger = structlog.get_logger()


class SGComponentRegistry:
    """SG组件注册表，统一管理单例生命周期

    特性：
    - 延迟初始化：首次get()时调用factory创建实例
    - 线程安全：双重检查锁保护
    - 可重置：支持单个/全部重置，便于测试隔离
    - 可替换：支持override factory，便于测试注入mock
    """

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        self._original_factories: dict[str, Callable[[], Any]] = {}
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
                        raise KeyError(
                            f"Component '{name}' not registered. "
                            f"Available: {list(self._factories.keys())}"
                        )
                    self._instances[name] = self._factories[name]()
        return self._instances[name]

    def reset(self, name: str | None = None) -> None:
        with self._lock:
            if name is None:
                self._instances.clear()
            else:
                self._instances.pop(name, None)

    def override(self, name: str, factory: Callable[[], Any]) -> None:
        with self._lock:
            if name not in self._factories:
                raise KeyError(
                    f"Cannot override unregistered component '{name}'. "
                    f"Available: {list(self._factories.keys())}"
                )
            if name not in self._original_factories:
                self._original_factories[name] = self._factories[name]
            self._factories[name] = factory
            self._instances.pop(name, None)

    def restore(self, name: str) -> None:
        with self._lock:
            if name in self._original_factories:
                self._factories[name] = self._original_factories.pop(name)
                self._instances.pop(name, None)

    def is_registered(self, name: str) -> bool:
        return name in self._factories

    def is_initialized(self, name: str) -> bool:
        return name in self._instances

    def list_registered(self) -> list[str]:
        return list(self._factories.keys())


sg_registry = SGComponentRegistry()
