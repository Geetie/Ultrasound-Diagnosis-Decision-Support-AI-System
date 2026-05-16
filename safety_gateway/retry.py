"""Safety Gateway通用重试工具模块

提供异步指数退避重试功能，统一consent_checker和threshold中的重试逻辑。
"""

import asyncio
import random
from typing import Any, Awaitable, Callable, TypeVar

import structlog

logger = structlog.get_logger()

T = TypeVar("T")


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_attempts: int = 3,
    delay_base: float = 0.5,
    max_total_time: float | None = None,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    log_prefix: str = "sg_retry",
    **kwargs: Any,
) -> T:
    start_time = asyncio.get_running_loop().time()

    for attempt in range(max_attempts):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as e:
            if attempt == max_attempts - 1:
                raise

            elapsed = asyncio.get_running_loop().time() - start_time
            delay = delay_base * (2 ** attempt)
            jitter = random.uniform(0, delay * 0.25)
            delay_with_jitter = delay + jitter

            if max_total_time is not None:
                estimated_remaining = elapsed + delay_with_jitter
                if estimated_remaining > max_total_time:
                    logger.warning(
                        f"{log_prefix}_exceeds_max_time",
                        attempt=attempt + 1,
                        elapsed=round(elapsed, 2),
                        max_total_time=max_total_time,
                        error=str(e),
                    )
                    raise

            await asyncio.sleep(delay_with_jitter)
            logger.warning(
                f"{log_prefix}_retry",
                attempt=attempt + 1,
                delay=round(delay_with_jitter, 2),
                elapsed=round(elapsed, 2),
                error=str(e),
            )

    raise RuntimeError("retry_with_backoff: unreachable")
