import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from safety_gateway.health.latency_tracker import LatencyTracker
from safety_gateway.health.memory_monitor import MemoryMonitor
from safety_gateway.health.fusion_probe import FusionProbe


class TestLatencyTracker:

    @pytest.mark.asyncio
    async def test_empty_stats(self):
        tracker = LatencyTracker()
        stats = await tracker.compute_stats()
        assert stats["samples"] == 0
        assert stats["p50_ms"] is None
        assert stats["p99_ms"] is None

    @pytest.mark.asyncio
    async def test_add_samples_and_compute(self):
        tracker = LatencyTracker()
        for i in range(100):
            await tracker.record(float(i))
        stats = await tracker.compute_stats()
        assert stats["samples"] == 100
        assert stats["p50_ms"] is not None
        assert stats["p99_ms"] is not None
        assert stats["min_ms"] == 0.0
        assert stats["max_ms"] == 99.0

    @pytest.mark.asyncio
    async def test_fifo_after_maxlen(self):
        tracker = LatencyTracker()
        for i in range(600):
            await tracker.record(float(i))
        stats = await tracker.compute_stats()
        assert stats["samples"] == 500

    @pytest.mark.asyncio
    async def test_e2e_independent_stats(self):
        tracker = LatencyTracker()
        await tracker.record(1.0)
        await tracker.record_e2e(5.0)
        stats = await tracker.compute_stats()
        e2e_stats = await tracker.compute_e2e_stats()
        assert stats["samples"] == 1
        assert e2e_stats["samples"] == 1

    @pytest.mark.asyncio
    async def test_p99_status_ok(self):
        tracker = LatencyTracker()
        status = tracker._get_p99_status(1.0, 4.0, 8.0)
        assert status == "ok"

    @pytest.mark.asyncio
    async def test_p99_status_warning(self):
        tracker = LatencyTracker()
        status = tracker._get_p99_status(5.0, 4.0, 8.0)
        assert status == "warning"

    @pytest.mark.asyncio
    async def test_p99_status_critical(self):
        tracker = LatencyTracker()
        status = tracker._get_p99_status(10.0, 4.0, 8.0)
        assert status == "critical"


class TestMemoryMonitor:

    def test_get_memory_status_ok(self):
        result = MemoryMonitor.get_memory_status(100.0, 512.0)
        assert result["status"] == "ok"
        assert result["rss_mb"] == 100.0

    def test_get_memory_status_over_budget(self):
        result = MemoryMonitor.get_memory_status(600.0, 512.0)
        assert result["status"] == "over_budget"

    def test_get_memory_status_unknown(self):
        result = MemoryMonitor.get_memory_status(-1.0, 512.0)
        assert result["status"] == "unknown"

    def test_get_memory_usage_mb_returns_float(self):
        result = MemoryMonitor.get_memory_usage_mb()
        assert isinstance(result, float)


class TestFusionProbe:

    @pytest.mark.asyncio
    async def test_healthy_returns_true(self):
        probe = FusionProbe()
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await probe.get_availability()
            assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy_returns_false(self):
        import httpx
        probe = FusionProbe()
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await probe.get_availability()
            assert result is False

    @pytest.mark.asyncio
    async def test_cache_ttl_no_recheck(self):
        probe = FusionProbe()
        probe._available = True
        probe._last_check_time = time.monotonic()
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            result = await probe.get_availability()
            assert result is True
            mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_expired_rechecks(self):
        probe = FusionProbe()
        probe._available = True
        probe._last_check_time = time.monotonic() - 100
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await probe.get_availability()
            assert result is True

    @pytest.mark.asyncio
    async def test_initial_state_is_none(self):
        probe = FusionProbe()
        assert probe.available is None
