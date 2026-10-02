import asyncio
import logging

import pytest

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]

logger = logging.getLogger(__name__)


class TestCircuitBreaker:

    def setup_method(self):
        from safety_gateway.circuit_breaker import CircuitBreakerError, circuit_breaker_manager
        self.manager = circuit_breaker_manager
        self.CircuitBreakerError = CircuitBreakerError
        self._breaker_names = set(self.manager._breakers.keys())
        logger.info("TestCircuitBreaker setup: existing breaker names=%s", self._breaker_names)

    def teardown_method(self):
        logger.info("TestCircuitBreaker teardown: removing breakers not in %s", self._breaker_names)
        for name in list(self.manager._breakers.keys()):
            if name not in self._breaker_names:
                del self.manager._breakers[name]

    def test_circuit_breaker_initial_state(self):
        breaker = self.manager.get_circuit_breaker("test", failure_threshold=5, success_threshold=3, cooldown_period=30.0)
        assert breaker.is_available is True
        assert breaker.state.value == "closed"

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_on_failures(self):
        breaker = self.manager.get_circuit_breaker("test_trip", failure_threshold=5, success_threshold=3, cooldown_period=30.0)

        async def failing_func():
            raise Exception("test failure")

        for _ in range(5):
            with pytest.raises(Exception):
                await breaker.execute(failing_func)

        assert breaker.state.value == "open"
        assert breaker.is_available is False

    @pytest.mark.asyncio
    async def test_circuit_breaker_fallback_on_open(self):
        breaker = self.manager.get_circuit_breaker("test_fallback", failure_threshold=5, success_threshold=3, cooldown_period=30.0)

        async def failing_func():
            raise Exception("test failure")

        for _ in range(5):
            with pytest.raises(Exception):
                await breaker.execute(failing_func)

        async def fallback_func():
            return "fallback_result"

        result = await breaker.execute(failing_func, fallback=fallback_func)
        assert result == "fallback_result"

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_on_success(self):
        breaker = self.manager.get_circuit_breaker("test_reset", failure_threshold=5, success_threshold=3, cooldown_period=0.01)

        async def failing_func():
            raise Exception("test failure")

        for _ in range(5):
            with pytest.raises(Exception):
                await breaker.execute(failing_func)

        assert breaker.state.value == "open"

        logger.info("test_circuit_breaker_resets_on_success: before asyncio.sleep(0.02), state=%s", breaker.state.value)
        await asyncio.sleep(0.02)
        logger.info("test_circuit_breaker_resets_on_success: after asyncio.sleep(0.02), state=%s", breaker.state.value)

        assert breaker.state.value == "half_open"

        async def success_func():
            return "success"

        await breaker.execute(success_func)
        await breaker.execute(success_func)
        await breaker.execute(success_func)

        assert breaker.state.value == "closed"
        assert breaker.is_available is True

    def test_circuit_breaker_manager_singleton(self):
        breaker1 = self.manager.get_circuit_breaker("singleton_test")
        breaker2 = self.manager.get_circuit_breaker("singleton_test")
        assert breaker1 is breaker2


class TestCircuitBreakerHealthCheck:

    def test_circuit_breaker_healthy_when_all_closed(self):
        from safety_gateway.circuit_breaker import circuit_breaker_manager
        from safety_gateway.health import HealthChecker

        circuit_breaker_manager.reset()

        checker = HealthChecker()
        result = checker._check_circuit_breaker()
        assert result["status"] == "healthy"
        assert "total_breakers" in result

    @pytest.mark.asyncio
    async def test_circuit_breaker_unhealthy_when_open(self):
        from safety_gateway.circuit_breaker import circuit_breaker_manager
        from safety_gateway.health import HealthChecker

        circuit_breaker_manager.reset()
        breaker = circuit_breaker_manager.get_circuit_breaker(
            "test_health_open", failure_threshold=1, cooldown_period=300.0
        )
        await breaker.record_failure()

        checker = HealthChecker()
        result = checker._check_circuit_breaker()
        assert result["status"] == "unhealthy"
        assert "test_health_open" in result["open_breakers"]

        circuit_breaker_manager.reset()


class TestCircuitBreakerHalfOpenRecovery:

    def setup_method(self):
        from safety_gateway.circuit_breaker import CircuitBreakerError, circuit_breaker_manager
        self.manager = circuit_breaker_manager
        self.CircuitBreakerError = CircuitBreakerError

    def teardown_method(self):
        self.manager.reset()

    @pytest.mark.asyncio
    async def test_half_open_to_open_on_failure(self):
        breaker = self.manager.get_circuit_breaker("test_ho_to_open", failure_threshold=2, success_threshold=3, cooldown_period=0.01)

        async def failing_func():
            raise Exception("test failure")

        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.execute(failing_func)
        assert breaker.state.value == "open"

        await asyncio.sleep(0.02)
        assert breaker.state.value == "half_open"

        with pytest.raises(Exception, match="test failure"):
            await breaker.execute(failing_func)
        assert breaker.state.value == "open"

    @pytest.mark.asyncio
    async def test_half_open_partial_success_then_failure(self):
        breaker = self.manager.get_circuit_breaker("test_ho_partial", failure_threshold=2, success_threshold=3, cooldown_period=0.01)

        async def failing_func():
            raise Exception("test failure")

        async def success_func():
            return "ok"

        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.execute(failing_func)
        assert breaker.state.value == "open"

        await asyncio.sleep(0.02)
        assert breaker.state.value == "half_open"

        await breaker.execute(success_func)
        await breaker.execute(success_func)
        assert breaker.state.value == "half_open"

        with pytest.raises(Exception, match="test failure"):
            await breaker.execute(failing_func)
        assert breaker.state.value == "open"

    @pytest.mark.asyncio
    async def test_cooldown_auto_transition_to_half_open(self):
        breaker = self.manager.get_circuit_breaker("test_cooldown_auto", failure_threshold=1, success_threshold=2, cooldown_period=0.01)

        async def failing_func():
            raise Exception("test failure")

        with pytest.raises(Exception):
            await breaker.execute(failing_func)
        assert breaker.state.value == "open"

        await asyncio.sleep(0.02)
        assert breaker.state.value == "half_open"

        async def success_func():
            return "ok"

        await breaker.execute(success_func)
        await breaker.execute(success_func)
        assert breaker.state.value == "closed"
