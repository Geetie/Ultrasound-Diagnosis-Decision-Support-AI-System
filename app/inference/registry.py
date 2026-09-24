import threading
from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger("app.inference.registry")


class LLMComponentRegistry:
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
                            f"LLM component '{name}' not registered. "
                            f"Available: {list(self._factories.keys())}"
                        )
                    self._instances[name] = self._factories[name]()
                    logger.debug("llm_component_initialized", name=name)
        return self._instances[name]

    def reset(self, name: str | None = None) -> None:
        with self._lock:
            if name is None:
                self._instances.clear()
                logger.debug("llm_registry_reset_all")
            else:
                self._instances.pop(name, None)
                logger.debug("llm_registry_reset", name=name)

    def override(self, name: str, factory: Callable[[], Any]) -> None:
        with self._lock:
            if name not in self._factories:
                raise KeyError(
                    f"Cannot override unregistered LLM component '{name}'. "
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


llm_registry = LLMComponentRegistry()


def _register_defaults() -> None:
    from app.inference.verification_config import VerificationConfig, get_default_verification_config

    def _create_verification_config() -> VerificationConfig:
        return get_default_verification_config()

    llm_registry.register("verification_config", _create_verification_config)

    from app.inference.hallucination_detector import HallucinationDetector
    llm_registry.register("hallucination_detector", HallucinationDetector)


_register_defaults()
