"""HSM 定时任务调度器。

负责 HSM 相关的定时任务调度和管理。
从 HSMManager 中提取，实现单一职责原则。
"""

import threading
from collections.abc import Callable

import structlog

logger = structlog.get_logger()


class TaskScheduler:
    """HSM 定时任务调度器。

    管理周期性和延迟性定时任务，提供统一的调度和清理接口。

    功能：
    - 调度周期性任务（自动重调度）
    - 调度延迟性任务（一次性）
    - 统一关闭所有定时器
    - 异常捕获和日志记录

    线程安全：通过内部锁保护定时器列表。
    """

    def __init__(self) -> None:
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()

    def schedule_periodic_task(
        self,
        task: Callable[[], None],
        interval_seconds: float,
        task_name: str = "unnamed",
    ) -> None:
        """调度周期性任务。

        任务执行完成后会自动重新调度，形成周期性执行。
        如果任务执行抛出异常，异常会被捕获并记录，不影响后续调度。

        Args:
            task: 要执行的任务函数（无参数）
            interval_seconds: 执行间隔（秒）
            task_name: 任务名称，用于日志标识
        """
        def _wrapper():
            try:
                task()
            except Exception as e:
                logger.error(
                    f"scheduled_task_failed_{task_name}",
                    error=str(e),
                )
            finally:
                # 重新调度下一轮
                with self._lock:
                    timer = threading.Timer(interval_seconds, _wrapper)
                    timer.daemon = True
                    timer.start()
                    self._timers.append(timer)

        with self._lock:
            timer = threading.Timer(interval_seconds, _wrapper)
            timer.daemon = True
            timer.start()
            self._timers.append(timer)

    def schedule_delayed_task(
        self,
        task: Callable[[], None],
        delay_seconds: float,
        task_name: str = "unnamed",
    ) -> None:
        """调度一次性延迟任务。

        Args:
            task: 要执行的任务函数（无参数）
            delay_seconds: 延迟时间（秒）
            task_name: 任务名称，用于日志标识
        """
        def _wrapper():
            try:
                task()
            except Exception as e:
                logger.error(
                    f"delayed_task_failed_{task_name}",
                    error=str(e),
                )

        with self._lock:
            timer = threading.Timer(delay_seconds, _wrapper)
            timer.daemon = True
            timer.start()
            self._timers.append(timer)

    def shutdown(self) -> None:
        """停止所有定时器。

        取消所有已调度但尚未执行的定时任务。
        正在执行的任务不会被中断。
        """
        with self._lock:
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
            logger.info("hsm_task_scheduler_shutdown")
