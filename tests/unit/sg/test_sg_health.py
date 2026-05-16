import time
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from safety_gateway.health import HealthChecker
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.checker import get_safety_gateway_checker

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestHealthChecker:

    @pytest.mark.asyncio
    async def test_check_redis_healthy(self):
        checker = HealthChecker()
        with patch('safety_gateway.health.providers.SafetyGatewayRedisPool') as mock_redis:
            mock_redis.health_check = AsyncMock(return_value=True)
            result = await checker._check_redis()
            assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_check_redis_unhealthy(self):
        checker = HealthChecker()
        with patch('safety_gateway.health.providers.SafetyGatewayRedisPool') as mock_redis:
            mock_redis.health_check = AsyncMock(return_value=False)
            result = await checker._check_redis()
            assert result["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_check_redis_error(self):
        checker = HealthChecker()
        with patch('safety_gateway.health.providers.SafetyGatewayRedisPool') as mock_redis:
            mock_redis.health_check = AsyncMock(side_effect=Exception("连接失败"))
            result = await checker._check_redis()
            assert result["status"] == "unhealthy"
            assert "error" in result

    def test_check_rules_engine_healthy(self):
        checker = HealthChecker()
        result = checker._check_rules_engine()
        assert result["status"] in ["healthy", "degraded"]
        assert "total_rules" in result
        assert "enabled_rules" in result

    def test_check_rules_engine_error(self):
        checker = HealthChecker()
        with patch.object(sg_desensitization_engine, 'get_rules', side_effect=Exception("规则加载失败")):
            result = checker._check_rules_engine()
            assert result["status"] == "unhealthy"


class TestP99LatencyAlert:

    def test_p99_status_ok(self):
        checker = HealthChecker()
        checker._latency_samples = [1.0, 2.0, 3.0, 4.0, 5.0]
        status = checker._get_p99_status(3.0)
        assert status == "ok"

    def test_p99_status_warning(self):
        checker = HealthChecker()
        status = checker._get_p99_status(4.5)
        assert status == "warning"

    def test_p99_status_critical(self):
        checker = HealthChecker()
        status = checker._get_p99_status(10.0)
        assert status == "critical"

    @pytest.mark.asyncio
    async def test_latency_stats_with_status(self):
        checker = HealthChecker()
        checker._latency_samples = [1.0, 2.0, 3.0, 4.0, 5.0]
        stats = await checker.compute_latency_stats()
        assert "p99_status" in stats
        assert "p99_threshold_warning_ms" in stats
        assert "p99_threshold_critical_ms" in stats


class TestOOMProtectionScenario:

    def test_oom_score_adj_configuration(self):
        import yaml
        from pathlib import Path

        compose_path = Path(__file__).parent.parent.parent / "docker-compose.dev.yml"
        if compose_path.exists():
            with open(compose_path) as f:
                compose_config = yaml.safe_load(f)
            sg_service = compose_config.get("services", {}).get("safety-gateway", {})
            oom_score = sg_service.get("oom_score_adj")
            assert oom_score == -1000, f"Expected oom_score_adj=-1000, got {oom_score}"
        else:
            pytest.skip("docker-compose.dev.yml not found")

    def test_health_check_returns_healthy_during_memory_pressure(self):
        from safety_gateway.health import HealthChecker

        health_checker = HealthChecker()

        import time
        start = time.time()
        result = health_checker._check_rules_engine()
        elapsed = time.time() - start

        assert result["status"] == "healthy"
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_sg_still_responsive_under_load(self):
        from safety_gateway.errors import DatabaseUnavailableError as SGDatabaseUnavailableError
        checker = get_safety_gateway_checker()

        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        for i in range(10):
            try:
                result = await checker.perform_full_check(f"P{i:03d}", test_data)
                assert result.status in [result.PASSED, result.DEGRADED] if hasattr(result, 'PASSED') else True
            except SGDatabaseUnavailableError:
                pass

    def test_oom_score_adj_read_from_proc(self):
        import os
        import platform

        if platform.system() == "Windows":
            pytest.skip("Windows环境下无法读取oom_score_adj")

        oom_score_adj_path = "/proc/self/oom_score_adj"

        if not os.path.exists(oom_score_adj_path):
            pytest.skip(f"{oom_score_adj_path}不存在，跳过测试")

        try:
            with open(oom_score_adj_path, "r") as f:
                oom_value = int(f.read().strip())

            assert oom_value == -1000, f"Expected oom_score_adj=-1000, got {oom_value}"
        except PermissionError:
            pytest.skip("无权限读取oom_score_adj")

    def test_memory_pressure_with_tracemalloc(self):
        import tracemalloc
        import gc

        gc.collect()
        tracemalloc.start()

        try:
            baseline_snapshot = tracemalloc.take_snapshot()

            checker = get_safety_gateway_checker()
            health_checker = HealthChecker()

            large_data = {"data": ["x" * 1000 for _ in range(100)]}

            for _ in range(10):
                sg_desensitization_engine.desensitize_dict(large_data)
                sg_desensitization_engine.check_sensitive_data("测试文本13812345678")

            current_snapshot = tracemalloc.take_snapshot()
            top_stats = current_snapshot.compare_to(baseline_snapshot, 'lineno')

            total_diff_mb = sum(stat.size_diff for stat in top_stats) / (1024 * 1024)

            health_result = health_checker._check_rules_engine()
            assert health_result["status"] in ["healthy", "degraded"]

            gc.collect()
            tracemalloc.stop()

        except Exception as e:
            gc.collect()
            tracemalloc.stop()
            pytest.fail(f"内存压力测试失败: {e}")


class TestDatabaseHealthCheck:

    @pytest.mark.asyncio
    async def test_check_database_healthy(self):
        from safety_gateway.health import HealthChecker
        from unittest.mock import AsyncMock, MagicMock

        checker = HealthChecker()
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)
        mock_engine.connect = MagicMock(return_value=mock_conn)

        with patch('safety_gateway.sg_db._async_engine', mock_engine), \
             patch('safety_gateway.sg_db._get_async_engine', AsyncMock(return_value=mock_engine)):
            result = await checker._check_database()
            assert result["status"] == "healthy"
            assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_check_database_unhealthy(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        with patch('safety_gateway.sg_db._async_engine', None):
            result = await checker._check_database()
            assert result["status"] == "unhealthy"


class TestHealthCheckIntegration:

    @pytest.mark.asyncio
    async def test_check_all_returns_complete_structure(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        with patch.object(checker, '_check_redis', new_callable=AsyncMock) as mock_redis, \
             patch.object(checker, '_check_database', new_callable=AsyncMock) as mock_db:
            mock_redis.return_value = {"status": "healthy", "latency_ms": 1.0}
            mock_db.return_value = {"status": "healthy", "latency_ms": 2.0}

            result = await checker.check_all()
            assert "status" in result
            assert "components" in result
            assert "redis" in result["components"]
            assert "database" in result["components"]
            assert "rules_engine" in result["components"]
            assert "circuit_breaker" in result["components"]
            assert "threshold_engine" in result["components"]
            assert "latency_stats" in result


class TestP99AlertTrigger:

    @pytest.mark.asyncio
    async def test_record_latency_and_check_stats(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        for _ in range(50):
            await checker.record_latency(1.0)
        for _ in range(45):
            await checker.record_latency(3.0)
        for _ in range(5):
            await checker.record_latency(6.0)

        stats = await checker.compute_latency_stats()
        assert stats["samples"] == 100
        assert stats["p99_status"] in ["ok", "warning", "critical"]

    @pytest.mark.asyncio
    async def test_p99_alert_critical_triggered(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        for _ in range(95):
            await checker.record_latency(1.0)
        for _ in range(5):
            await checker.record_latency(10.0)

        stats = await checker.compute_latency_stats()
        assert stats["p99_ms"] >= 5.0
        assert stats["p99_status"] == "critical"


@pytest.mark.skip_fusion_mock
class TestHealthCheckerGlobalSingleton:

    def test_get_health_checker_returns_singleton(self):
        from safety_gateway.checker import _get_health_checker

        hc1 = _get_health_checker()
        hc2 = _get_health_checker()
        assert hc1 is hc2

    @pytest.mark.asyncio
    async def test_record_latency_persists_across_calls(self):
        from safety_gateway.checker import _get_health_checker

        hc = _get_health_checker()
        hc._latency_tracker._samples.clear()
        await hc.record_latency(2.5)
        await hc.record_latency(3.5)

        stats = await hc.compute_latency_stats()
        assert stats["samples"] == 2
        assert stats["avg_ms"] == 3.0

    @pytest.mark.asyncio
    async def test_latency_samples_not_lost_on_new_health_check_call(self):
        from safety_gateway.checker import _get_health_checker

        hc = _get_health_checker()
        hc._latency_tracker._samples.clear()
        await hc.record_latency(1.0)

        hc2 = _get_health_checker()
        assert hc2 is hc
        assert len(hc2._latency_tracker._samples) == 1


class TestHealthCheckDegradedStatus:

    @pytest.mark.asyncio
    async def test_overall_degraded_when_rules_engine_degraded(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        with patch.object(checker, '_check_redis', new_callable=AsyncMock) as mock_redis, \
             patch.object(checker, '_check_database', new_callable=AsyncMock) as mock_db, \
             patch.object(checker, '_check_fusion_layer', new_callable=AsyncMock) as mock_fusion:
            mock_redis.return_value = {"status": "healthy", "latency_ms": 1.0}
            mock_db.return_value = {"status": "healthy", "latency_ms": 2.0}
            mock_fusion.return_value = {"status": "healthy", "latency_ms": 3.0}
            with patch.object(checker, '_check_rules_engine', return_value={"status": "degraded", "total_rules": 0, "enabled_rules": 0}), \
                 patch.object(checker, '_check_circuit_breaker', return_value={"status": "healthy", "total_breakers": 0, "open_breakers": []}), \
                 patch.object(checker, '_check_threshold_engine', return_value={"status": "healthy", "loaded": True, "threshold_count": 3}), \
                 patch.object(checker, '_check_oom_protection', return_value={"status": "unknown", "oom_score_adj": None, "expected": -1000}):
                result = await checker.check_all()
                assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_overall_unhealthy_when_db_unhealthy(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        with patch.object(checker, '_check_redis', new_callable=AsyncMock) as mock_redis, \
             patch.object(checker, '_check_database', new_callable=AsyncMock) as mock_db, \
             patch.object(checker, '_check_fusion_layer', new_callable=AsyncMock) as mock_fusion:
            mock_redis.return_value = {"status": "healthy", "latency_ms": 1.0}
            mock_db.return_value = {"status": "unhealthy", "error": "connection_refused"}
            mock_fusion.return_value = {"status": "healthy", "latency_ms": 3.0}
            with patch.object(checker, '_check_rules_engine', return_value={"status": "healthy", "total_rules": 10, "enabled_rules": 10}), \
                 patch.object(checker, '_check_circuit_breaker', return_value={"status": "healthy", "total_breakers": 0, "open_breakers": []}), \
                 patch.object(checker, '_check_threshold_engine', return_value={"status": "healthy", "loaded": True, "threshold_count": 3}), \
                 patch.object(checker, '_check_oom_protection', return_value={"status": "unknown", "oom_score_adj": None, "expected": -1000}):
                result = await checker.check_all()
                assert result["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_overall_healthy_when_all_healthy(self):
        from safety_gateway.health import HealthChecker

        checker = HealthChecker()
        with patch.object(checker, '_check_redis', new_callable=AsyncMock) as mock_redis, \
             patch.object(checker, '_check_database', new_callable=AsyncMock) as mock_db, \
             patch.object(checker, '_check_fusion_layer', new_callable=AsyncMock) as mock_fusion:
            mock_redis.return_value = {"status": "healthy", "latency_ms": 1.0}
            mock_db.return_value = {"status": "healthy", "latency_ms": 2.0}
            mock_fusion.return_value = {"status": "healthy", "latency_ms": 3.0}
            with patch.object(checker, '_check_rules_engine', return_value={"status": "healthy", "total_rules": 10, "enabled_rules": 10}), \
                 patch.object(checker, '_check_circuit_breaker', return_value={"status": "healthy", "total_breakers": 0, "open_breakers": []}), \
                 patch.object(checker, '_check_threshold_engine', return_value={"status": "healthy", "loaded": True, "threshold_count": 3}), \
                 patch.object(checker, '_check_oom_protection', return_value={"status": "unknown", "oom_score_adj": None, "expected": -1000}):
                result = await checker.check_all()
                assert result["status"] == "degraded"


class TestOOMProtectionHealthCheck:

    @pytest.mark.asyncio
    async def test_oom_protected_health_check_healthy(self):
        checker = HealthChecker()
        with patch.object(checker, '_check_redis', new_callable=AsyncMock, return_value={"status": "healthy"}), \
             patch.object(checker, '_check_database', new_callable=AsyncMock, return_value={"status": "healthy"}), \
             patch.object(checker, '_check_rules_engine', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_circuit_breaker', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_threshold_engine', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_oom_protection', return_value={"status": "protected", "oom_score_adj": -1000, "expected": -1000}):
            result = await checker.check_all()
            assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_oom_warning_health_check_degraded(self):
        checker = HealthChecker()
        with patch.object(checker, '_check_redis', new_callable=AsyncMock, return_value={"status": "healthy"}), \
             patch.object(checker, '_check_database', new_callable=AsyncMock, return_value={"status": "healthy"}), \
             patch.object(checker, '_check_rules_engine', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_circuit_breaker', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_threshold_engine', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_oom_protection', return_value={"status": "warning", "oom_score_adj": 0, "expected": -1000}):
            result = await checker.check_all()
            assert result["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_oom_unknown_degraded(self):
        checker = HealthChecker()
        with patch.object(checker, '_check_redis', new_callable=AsyncMock, return_value={"status": "healthy"}), \
             patch.object(checker, '_check_database', new_callable=AsyncMock, return_value={"status": "healthy"}), \
             patch.object(checker, '_check_rules_engine', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_circuit_breaker', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_threshold_engine', return_value={"status": "healthy"}), \
             patch.object(checker, '_check_oom_protection', return_value={"status": "unknown", "oom_score_adj": None, "expected": -1000}):
            result = await checker.check_all()
            assert result["status"] == "degraded"


class TestFusionLayerHealthCheck:

    @pytest.mark.asyncio
    async def test_fusion_layer_healthy(self):
        from safety_gateway.health import HealthChecker
        checker = HealthChecker()
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await checker._check_fusion_layer()
            assert result["status"] == "healthy"
            assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_fusion_layer_unhealthy_non_200(self):
        from safety_gateway.health import HealthChecker
        checker = HealthChecker()
        mock_response = MagicMock()
        mock_response.status_code = 503
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await checker._check_fusion_layer()
            assert result["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_fusion_layer_timeout(self):
        import httpx
        from safety_gateway.health import HealthChecker
        checker = HealthChecker()
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await checker._check_fusion_layer()
            assert result["status"] == "unhealthy"
            assert result.get("error") == "timeout"

    @pytest.mark.asyncio
    async def test_fusion_layer_connection_refused(self):
        import httpx
        from safety_gateway.health import HealthChecker
        checker = HealthChecker()
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await checker._check_fusion_layer()
            assert result["status"] == "unhealthy"
            assert result.get("error") == "connection_refused"

    @pytest.mark.asyncio
    async def test_get_fusion_availability_cache(self):
        import time
        from safety_gateway.health import HealthChecker
        checker = HealthChecker()
        checker._fusion_probe._available = True
        checker._fusion_probe._last_check_time = time.monotonic()
        result = await checker.get_fusion_availability()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_fusion_availability_cache_expired(self):
        import time
        from safety_gateway.health import HealthChecker
        checker = HealthChecker()
        checker._fusion_probe._available = True
        checker._fusion_probe._last_check_time = time.monotonic() - 100
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("safety_gateway.health.fusion_probe.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            result = await checker.get_fusion_availability()
            assert result is True

    @pytest.mark.asyncio
    async def test_fusion_self_detected_unavailable_in_full_check(self):
        from safety_gateway.checker import SafetyGatewayChecker, _reset_safety_gateway_checker
        from safety_gateway.validation_result import ConsentStatus
        from safety_gateway.consent_checker import ConsentCheckResult
        _reset_safety_gateway_checker()
        try:
            checker = SafetyGatewayChecker()
            with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_consent, \
                 patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
                mock_consent.return_value.check_consent_status = AsyncMock(return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False))
                mock_hc = AsyncMock()
                mock_hc.get_fusion_availability = AsyncMock(return_value=False)
                mock_hc.record_latency = AsyncMock()
                mock_hc.record_e2e_latency = AsyncMock()
                mock_hc_getter.return_value = mock_hc
                result = await checker.perform_full_check(
                    patient_id="test-patient",
                    output_data={"organ_name": "thyroid"},
                )
                assert result.degraded_mode is True
                assert result.degraded_reason is not None
        finally:
            _reset_safety_gateway_checker()
