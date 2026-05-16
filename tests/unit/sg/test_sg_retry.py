import asyncio

import pytest

from safety_gateway.retry import retry_with_backoff

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestRetryWithBackoff:

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        call_count = 0

        async def success_func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry_with_backoff(success_func, max_attempts=3)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary")
            return "ok"

        result = await retry_with_backoff(
            flaky_func,
            max_attempts=3,
            delay_base=0.01,
            retryable_exceptions=(ConnectionError,),
        )
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_max_attempts_exhausted(self):
        async def always_fail():
            raise ConnectionError("persistent")

        with pytest.raises(ConnectionError, match="persistent"):
            await retry_with_backoff(
                always_fail,
                max_attempts=2,
                delay_base=0.01,
                retryable_exceptions=(ConnectionError,),
            )

    @pytest.mark.asyncio
    async def test_max_total_time_exceeded(self):
        call_count = 0

        async def slow_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("slow failure")

        with pytest.raises(ConnectionError):
            await retry_with_backoff(
                slow_fail,
                max_attempts=10,
                delay_base=0.5,
                max_total_time=0.01,
                retryable_exceptions=(ConnectionError,),
            )

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_non_retryable_exception_not_retried(self):
        call_count = 0

        async def value_error_func():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            await retry_with_backoff(
                value_error_func,
                max_attempts=3,
                delay_base=0.01,
                retryable_exceptions=(ConnectionError,),
            )

        assert call_count == 1
