import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.observability.constants import DiskAlertThresholds, EdgeGPUConstants
from app.observability.infra_health import (
    check_disk_usage,
    check_edge_gpu_temperature,
    check_pg_health,
    check_redis_health,
    run_infra_health_checks,
    stop_infra_health_checks,
)


@pytest.fixture(autouse=True)
def _reset_globals():
    import app.observability.infra_health as mod

    mod._infra_shutdown_event = None
    mod._edge_gpu_overheat_since = None
    yield
    mod._infra_shutdown_event = None
    mod._edge_gpu_overheat_since = None


@pytest.fixture()
def mock_collector():
    with patch("app.observability.infra_health.get_metrics_collector") as m:
        collector = MagicMock()
        m.return_value = collector
        yield collector


@pytest.mark.track_obs
class TestCheckRedisHealth:
    @pytest.mark.asyncio
    async def test_success_ping_true(self, mock_collector):
        fake_client = AsyncMock()
        fake_client.ping = AsyncMock(return_value=True)
        fake_client.aclose = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=fake_client):
            result = await check_redis_health("redis://localhost:6379")

        assert result is True
        mock_collector.set_redis_process_alive.assert_called_with(True)

    @pytest.mark.asyncio
    async def test_failure_connection_exception(self, mock_collector):
        with patch("redis.asyncio.from_url", side_effect=Exception("conn refused")):
            result = await check_redis_health("redis://bad:6379")

        assert result is False
        mock_collector.set_redis_process_alive.assert_called_with(False)


@pytest.mark.track_obs
class TestCheckPgHealth:
    @pytest.mark.asyncio
    async def test_success_pool_utilization_and_slow_queries(self, mock_collector):
        fake_conn = AsyncMock()
        fake_conn.fetchval = AsyncMock(return_value=5)

        fake_pool = MagicMock()
        fake_pool.acquire = MagicMock()
        fake_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        fake_pool.get_stats = MagicMock(return_value={})
        fake_pool.get_idle_size = MagicMock(return_value=2)
        fake_pool.get_max_size = MagicMock(return_value=10)
        fake_pool.close = AsyncMock()

        with patch("asyncpg.create_pool", AsyncMock(return_value=fake_pool)):
            result = await check_pg_health("postgresql://localhost/db")

        assert result["pool_utilization"] == pytest.approx(0.8)
        assert result["slow_queries_detected"] is True
        mock_collector.set_pg_connection_pool_utilization.assert_called_with(0.8)
        mock_collector.inc_pg_slow_queries.assert_called_once()
        fake_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pool_exception_owns_pool_closed(self, mock_collector):
        fake_pool = MagicMock()
        fake_pool.acquire = MagicMock(side_effect=Exception("acquire failed"))
        fake_pool.close = AsyncMock()

        with patch("asyncpg.create_pool", AsyncMock(return_value=fake_pool)):
            result = await check_pg_health("postgresql://localhost/db")

        assert result["pool_utilization"] == 0.0
        fake_pool.close.assert_awaited_once()


@pytest.mark.track_obs
class TestCheckDiskUsage:
    def test_normal(self, mock_collector):
        usage = MagicMock()
        usage.used = 50
        usage.total = 100

        with patch("app.observability.infra_health.shutil.disk_usage", return_value=usage):
            result = check_disk_usage("/data")

        assert result["alert_level"] == "normal"
        mock_collector.set_disk_usage_ratio.assert_called_with("/data", 0.5)

    def test_warning(self, mock_collector):
        usage = MagicMock()
        usage.used = 85
        usage.total = 100

        with patch("app.observability.infra_health.shutil.disk_usage", return_value=usage):
            result = check_disk_usage("/data")

        assert result["alert_level"] == "warning"

    def test_critical(self, mock_collector):
        usage = MagicMock()
        usage.used = 95
        usage.total = 100

        with patch("app.observability.infra_health.shutil.disk_usage", return_value=usage):
            result = check_disk_usage("/data")

        assert result["alert_level"] == "critical"


@pytest.mark.track_obs
class TestCheckEdgeGpuTemperature:
    def test_normal(self, mock_collector):
        result = check_edge_gpu_temperature(50.0)
        assert result["alert_level"] == "normal"
        mock_collector.set_edge_gpu_temperature.assert_called_with(50.0)
        mock_collector.set_edge_gpu_overheat_duration.assert_called_with(0.0)

    def test_warning(self, mock_collector):
        result = check_edge_gpu_temperature(85.0)
        assert result["alert_level"] == "warning"
        mock_collector.set_edge_gpu_overheat_duration.assert_called_with(0.0)

    def test_critical_sustained(self, mock_collector):
        import app.observability.infra_health as mod

        now = time.time()
        mod._edge_gpu_overheat_since = now - EdgeGPUConstants.OVERHEAT_DURATION_SECONDS - 1

        result = check_edge_gpu_temperature(95.0)
        assert result["alert_level"] == "critical"
        mock_collector.set_edge_gpu_overheat_duration.assert_called_once()

    def test_critical_not_yet_sustained(self, mock_collector):
        import app.observability.infra_health as mod

        now = time.time()
        mod._edge_gpu_overheat_since = now - 5

        result = check_edge_gpu_temperature(92.0)
        assert result["alert_level"] == "warning"


@pytest.mark.track_obs
class TestRunInfraHealthChecks:
    @pytest.mark.asyncio
    async def test_loop_stopped_by_shutdown(self, mock_collector):
        with (
            patch("app.observability.infra_health.check_redis_health", new_callable=AsyncMock) as mock_redis,
            patch("app.observability.infra_health.check_pg_health", new_callable=AsyncMock) as mock_pg,
        ):
            call_count = 0

            async def _counting_redis(*a, **kw):
                nonlocal call_count
                call_count += 1

            mock_redis.side_effect = _counting_redis

            async def _stop_after_first_check():
                nonlocal call_count
                while call_count < 1:
                    await asyncio.sleep(0.01)
                await stop_infra_health_checks()

            task = asyncio.create_task(run_infra_health_checks(interval_seconds=0.01))
            stop_task = asyncio.create_task(_stop_after_first_check())

            try:
                await asyncio.wait_for(asyncio.gather(task, stop_task), timeout=2.0)
            except asyncio.TimeoutError:
                await stop_infra_health_checks()
                await asyncio.sleep(0.1)

        assert call_count >= 1
