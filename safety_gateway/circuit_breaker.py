from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from inspect import iscoroutine
from typing import Any
import asyncio

import structlog

from safety_gateway.errors import CircuitBreakerError

logger = structlog.get_logger()


class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    熔断器模式实现，用于保护系统免受频繁失败的影响
    
    状态转换:
    CLOSED → OPEN: 失败次数达到阈值
    OPEN → HALF_OPEN: 经过冷却时间后
    HALF_OPEN → CLOSED: 成功次数达到阈值
    HALF_OPEN → OPEN: 再次失败

    部署约束：SafetyGateway必须以uvicorn --workers 1模式运行。
    若改为多worker部署，_update_state()和execute()中的状态检查-执行流程
    将产生TOCTOU竞态，必须引入分布式锁或进程间同步机制。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        cooldown_period: float = 30.0,
        reset_timeout: float = 60.0,
    ):
        """
        初始化熔断器
        
        :param failure_threshold: 触发熔断的失败次数阈值
        :param success_threshold: 从半开状态恢复到闭合状态所需的成功次数
        :param cooldown_period: 从打开状态转换到半开状态的冷却时间（秒）
        :param reset_timeout: 重置计数器的超时时间（秒）
        """
        self._failure_threshold = failure_threshold
        self._success_threshold = success_threshold
        self._cooldown_period = cooldown_period
        self._reset_timeout = reset_timeout

        self._state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: datetime | None = None
        self._last_state_change_time: datetime = datetime.now(timezone.utc)
        self._open_time: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitBreakerState:
        self._update_state()
        return self._state

    @property
    def is_available(self) -> bool:
        self._update_state()
        return self._state != CircuitBreakerState.OPEN

    def _update_state(self) -> None:
        """更新熔断器状态（检查冷却时间是否到期）。

        asyncio单线程约束：此方法修改self._state等可变状态，
        依赖asyncio事件循环的单线程特性保证线程安全。
        state/is_available属性在execute()等async方法中被调用，
        与record_failure/record_success持有_lock的代码不会并发执行。

        注意：state/is_available属性是同步property，理论上可从
        非async上下文调用（如get_stats()），但仅执行读取操作，
        不修改状态，因此不会产生竞态条件。

        uvicorn --workers 1 约束：SafetyGateway以单worker模式运行，
        确保所有请求在同一进程的同一事件循环中处理。
        若改为多worker部署，此方法及execute()中的状态检查-执行流程
        将产生TOCTOU竞态，必须引入分布式锁或进程间同步机制。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "circuit_breaker_update_state_outside_asyncio",
                note="_update_state() called outside asyncio event loop; "
                     "safe for read-only access but state mutations may race in multi-worker deployments",
            )
        now = datetime.now(timezone.utc)

        if self._state == CircuitBreakerState.OPEN:
            if self._open_time is not None:
                elapsed = (now - self._open_time).total_seconds()
                if elapsed >= self._cooldown_period:
                    self._transition_to_half_open()

        elif self._state == CircuitBreakerState.CLOSED and self._last_failure_time is not None:
            elapsed = (now - self._last_failure_time).total_seconds()
            if elapsed >= self._reset_timeout:
                self._reset_counters()

    def _transition_to_open(self) -> None:
        self._state = CircuitBreakerState.OPEN
        self._open_time = datetime.now(timezone.utc)
        self._last_state_change_time = self._open_time
        self._failure_count = 0
        logger.warning("circuit_breaker_opened")

    def _transition_to_half_open(self) -> None:
        self._state = CircuitBreakerState.HALF_OPEN
        self._last_state_change_time = datetime.now(timezone.utc)
        self._success_count = 0
        logger.info("circuit_breaker_half_open")

    def _transition_to_closed(self) -> None:
        self._state = CircuitBreakerState.CLOSED
        self._last_state_change_time = datetime.now(timezone.utc)
        self._failure_count = 0
        self._success_count = 0
        logger.info("circuit_breaker_closed")

    def _reset_counters(self) -> None:
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None

    async def record_failure(self) -> None:
        async with self._lock:
            self._last_failure_time = datetime.now(timezone.utc)

            if self._state == CircuitBreakerState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._transition_to_open()

            elif self._state == CircuitBreakerState.HALF_OPEN:
                self._transition_to_open()

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    self._transition_to_closed()

            elif self._state == CircuitBreakerState.CLOSED:
                self._reset_counters()

    async def execute(
        self,
        func: Callable[..., Any],
        *args,
        fallback: Callable[..., Any] | None = None,
        **kwargs
    ) -> Any:
        """
        执行受熔断器保护的操作
        
        :param func: 要执行的异步函数
        :param fallback: 熔断器打开时的回退函数
        :param args: 函数参数
        :param kwargs: 函数关键字参数
        :return: 函数返回值或回退函数返回值
        """
        if not self.is_available:
            if fallback is not None:
                logger.warning("circuit_breaker_open_using_fallback")
                result = fallback(*args, **kwargs)
                if iscoroutine(result):
                    return await result
                return result
            raise CircuitBreakerError("Circuit breaker is open")

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception:
            await self.record_failure()
            raise

    def get_stats(self) -> dict[str, Any]:
        """获取熔断器统计信息"""
        now = datetime.now(timezone.utc)
        time_in_state = (now - self._last_state_change_time).total_seconds()

        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self._failure_threshold,
            "success_threshold": self._success_threshold,
            "cooldown_period": self._cooldown_period,
            "time_in_state_seconds": round(time_in_state, 2),
            "last_failure_time": self._last_failure_time.isoformat() if self._last_failure_time else None,
            "last_state_change_time": self._last_state_change_time.isoformat(),
        }



class CircuitBreakerManager:
    """熔断器管理器，管理多个熔断器实例"""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_circuit_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        cooldown_period: float = 30.0,
    ) -> CircuitBreaker:
        """获取或创建熔断器实例"""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                failure_threshold=failure_threshold,
                success_threshold=success_threshold,
                cooldown_period=cooldown_period,
            )
            logger.info("circuit_breaker_created", name=name)
        return self._breakers[name]

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """获取所有熔断器的统计信息"""
        return {name: breaker.get_stats() for name, breaker in self._breakers.items()}

    def reset(self, name: str | None = None) -> None:
        """重置熔断器"""
        if name is None:
            self._breakers = {}
        elif name in self._breakers:
            del self._breakers[name]


from safety_gateway.registry import sg_registry


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    if not sg_registry.is_registered("circuit_breaker_manager"):
        sg_registry.register("circuit_breaker_manager", CircuitBreakerManager)
    return sg_registry.get("circuit_breaker_manager")


def _reset_circuit_breaker_manager() -> None:
    sg_registry.reset("circuit_breaker_manager")


circuit_breaker_manager = get_circuit_breaker_manager()
