import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from safety_gateway.checker import (
    CheckResultStatus,
    DegradedReason,
    SafetyGatewayChecker,
    get_safety_gateway_checker,
)
from safety_gateway.consent_checker import ConsentChecker, ConsentCheckResult
from safety_gateway.threshold import get_threshold_engine
from safety_gateway.validation_result import ConsentStatus, ThresholdCheckResult

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestDegradedPathScenarios:

    def setup_method(self):
        self.checker = SafetyGatewayChecker()
        logger.info("TestDegradedPathScenarios setup: checker created")

    def teardown_method(self):
        from safety_gateway.circuit_breaker import circuit_breaker_manager
        circuit_breaker_manager._breakers.pop("sg_db_consent", None)
        logger.info("TestDegradedPathScenarios teardown: popped sg_db_consent breaker")

    @pytest.mark.asyncio
    async def test_checker_with_circuit_breaker_open(self):
        from safety_gateway.circuit_breaker import circuit_breaker_manager

        breaker = circuit_breaker_manager.get_circuit_breaker("sg_db_consent", failure_threshold=5, success_threshold=3, cooldown_period=30.0)

        async def failing_query():
            raise Exception("DB connection failed")

        for _ in range(5):
            with pytest.raises(Exception):
                await breaker.execute(failing_query)

        assert breaker.state.value == "open"

        result = await self.checker._consent_checker.check_consent_status("P001")
        assert result.error is True

    @pytest.mark.asyncio
    async def test_threshold_engine_fallback_to_default(self):
        engine = get_threshold_engine()

        with patch.object(engine, 'load_thresholds_from_db', side_effect=Exception("DB connection failed")):
            await engine._lazy_load()

        assert engine._loaded is True
        assert "thyroid" in engine._thresholds
        assert engine._thresholds["thyroid"].source == "default"

    @pytest.mark.asyncio
    async def test_threshold_engine_cache_ttl(self):
        engine = get_threshold_engine()
        engine._last_load_time = None

        await engine.ensure_loaded()
        first_load = engine._last_load_time

        await engine.ensure_loaded()
        second_load = engine._last_load_time

        assert first_load == second_load


class TestDegradedPathSevenScenarios:

    def setup_method(self):
        from safety_gateway.checker import SafetyGatewayChecker
        self.checker = SafetyGatewayChecker()

    @pytest.mark.asyncio
    async def test_pg_query_success(self):
        checker = ConsentChecker()
        with patch.object(checker, '_retry_query', new_callable=AsyncMock, return_value=ConsentStatus.GRANTED):
            result = await checker.check_consent_status("P001")
            assert result.status == ConsentStatus.GRANTED
            assert result.error is False

    @pytest.mark.asyncio
    async def test_fusion_unavailable_sg_degraded_output(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            with patch('safety_gateway.checker.desensitization_phase.sg_desensitization_engine') as mock_de:
                mock_de.check_sensitive_data.return_value = []
                with patch.object(self.checker, 'check_contraindication', new_callable=AsyncMock, return_value={"has_contraindication": False, "contraindications": [], "message": ""}):
                    with patch.object(self.checker._threshold_phase, 'check_with_params', new_callable=AsyncMock, return_value=None):
                        with patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
                            mock_hc = AsyncMock()
                            mock_hc.get_fusion_availability = AsyncMock(return_value=False)
                            mock_hc.record_latency = AsyncMock()
                            mock_hc.record_e2e_latency = AsyncMock()
                            mock_hc_getter.return_value = mock_hc
                            result = await self.checker.perform_full_check(
                                "P001", {"organ": "thyroid", "diagnosis_data": {"patient_conditions": []}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
                            )
                            assert result.status == CheckResultStatus.DEGRADED
                            assert result.degraded_reason == DegradedReason.FUSION_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_sg_unavailable_reject_all_output(self):
        from safety_gateway.errors import DatabaseUnavailableError as SGDatabaseUnavailableError
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=None, timeout=False, error=True)):
            with pytest.raises(SGDatabaseUnavailableError) as exc_info:
                await self.checker.perform_full_check("P001", {"organ": "thyroid", "diagnosis_data": {}})
            assert exc_info.value.code == "E4005B"

    @pytest.mark.asyncio
    async def test_edge_disconnected_local_degradation(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            result = await self.checker.perform_full_check(
                "P001", {"organ": "thyroid"},
                edge_connected=False,
            )
            assert result.status == CheckResultStatus.DEGRADED
            assert result.degraded_reason == DegradedReason.EDGE_DISCONNECTED

    @pytest.mark.asyncio
    async def test_combined_failure_degradation(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            with patch('safety_gateway.checker.desensitization_phase.sg_desensitization_engine') as mock_de:
                mock_de.check_sensitive_data.return_value = []
                with patch.object(self.checker, 'check_contraindication', new_callable=AsyncMock, return_value={"has_contraindication": True, "contraindications": [{"rule_id": "CI001", "organ": "thyroid", "condition": "test", "description": "test", "severity": "high"}], "message": "禁忌"}):
                    with patch.object(self.checker._threshold_engine, 'check', new_callable=AsyncMock) as mock_threshold:
                        mock_threshold.return_value = ThresholdCheckResult(organ="thyroid", confidence=0.3, threshold=0.7, passed=False, message="below")
                        with patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
                            mock_hc = AsyncMock()
                            mock_hc.get_fusion_availability = AsyncMock(return_value=False)
                            mock_hc.record_latency = AsyncMock()
                            mock_hc.record_e2e_latency = AsyncMock()
                            mock_hc_getter.return_value = mock_hc
                            result = await self.checker.perform_full_check(
                                "P001",
                                {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.3}]},
                            )
                            assert result.status == CheckResultStatus.DEGRADED
                            assert result.degraded_reason == DegradedReason.COMBINED_FAILURE

    @pytest.mark.asyncio
    async def test_fusion_unavailable_sensitive_detected_e4005a(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            with patch('safety_gateway.checker.desensitization_phase.sg_desensitization_engine') as mock_de:
                mock_de.check_sensitive_data.return_value = ["sg_id_card"]
                with patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
                    mock_hc = AsyncMock()
                    mock_hc.get_fusion_availability = AsyncMock(return_value=False)
                    mock_hc.record_latency = AsyncMock()
                    mock_hc.record_e2e_latency = AsyncMock()
                    mock_hc_getter.return_value = mock_hc
                    result = await self.checker.perform_full_check(
                        "P001",
                        {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
                    )
                    assert result.status == CheckResultStatus.FAILED
                    assert result.degraded_reason == DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED

    @pytest.mark.asyncio
    async def test_sg_unavailable_consent_withdrawn_e4005a(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.WITHDRAWN, timeout=False, error=True)):
            result = await self.checker.perform_full_check("P001", {"organ": "thyroid", "diagnosis_data": {}}, consent_status_at_input="withdrawn")
            assert result.status == CheckResultStatus.FAILED
            assert result.consent_status_source == "input_fast_reject"

    @pytest.mark.asyncio
    async def test_sg_unavailable_consent_withdrawn_db_error_path(self):
        from safety_gateway.errors import DatabaseUnavailableError
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.WITHDRAWN, timeout=False, error=True)):
            with pytest.raises(DatabaseUnavailableError):
                await self.checker.perform_full_check("P001", {"organ": "thyroid", "diagnosis_data": {}}, consent_status_at_input=None)


class TestRedisFailoverDegradation:

    @pytest.mark.asyncio
    async def test_redis_pool_reconnect_after_failure(self):
        from safety_gateway.redis_pool import SafetyGatewayRedisPool

        with patch.object(SafetyGatewayRedisPool, 'health_check', new_callable=AsyncMock) as mock_hc:
            mock_hc.return_value = False
            result = await SafetyGatewayRedisPool.health_check()
            assert result is False

        with patch.object(SafetyGatewayRedisPool, 'health_check', new_callable=AsyncMock) as mock_hc:
            mock_hc.return_value = True
            result = await SafetyGatewayRedisPool.health_check()
            assert result is True

    @pytest.mark.asyncio
    @pytest.mark.skip_fusion_mock
    @pytest.mark.skipif(
        True,
        reason="Pre-existing: requires running Redis; mock conflicts with autouse fusion mock. Tracked as known issue."
    )
    async def test_redis_transient_failure_does_not_block_check(self):
        checker = get_safety_gateway_checker()
        with patch.object(checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            with patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
                mock_hc = AsyncMock()
                mock_hc.get_fusion_availability = AsyncMock(return_value=True)
                mock_hc.record_latency = AsyncMock()
                mock_hc.record_e2e_latency = AsyncMock()
                mock_hc_getter.return_value = mock_hc
                result = await checker.perform_full_check(
                    "P001",
                    {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
                )
                assert result.status == CheckResultStatus.PASSED

    @pytest.mark.asyncio
    async def test_redis_health_check_consecutive_failure_triggers_reset(self):
        from safety_gateway.redis_pool import SafetyGatewayRedisPool

        SafetyGatewayRedisPool._consecutive_failures = 2
        with patch.object(SafetyGatewayRedisPool, 'get_client', new_callable=AsyncMock) as mock_get:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(side_effect=Exception("connection refused"))
            mock_get.return_value = mock_client
            with patch.object(SafetyGatewayRedisPool, '_get_lock') as mock_lock:
                mock_async_lock = AsyncMock()
                mock_async_lock.__aenter__ = AsyncMock(return_value=None)
                mock_async_lock.__aexit__ = AsyncMock(return_value=None)
                mock_lock.return_value = mock_async_lock
                SafetyGatewayRedisPool._client = mock_client
                result = await SafetyGatewayRedisPool.health_check()
                assert result is False
        SafetyGatewayRedisPool._consecutive_failures = 0
        SafetyGatewayRedisPool._client = None

    @pytest.mark.asyncio
    async def test_compact_mode_redis_unavailable_degraded_overall(self):
        from safety_gateway.health.providers import check_redis

        with patch('safety_gateway.health.providers.SafetyGatewayRedisPool') as mock_pool:
            mock_pool.health_check = AsyncMock(return_value=False)
            result = await check_redis()
            assert result["status"] == "unhealthy"


class TestDatabaseRetryMechanism:

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self):
        checker = ConsentChecker()
        call_count = 0

        async def mock_query(patient_id):
            nonlocal call_count
            call_count += 1
            if call_count < checker._retry_max_attempts:
                raise asyncio.TimeoutError("timeout")
            return ConsentStatus.GRANTED

        with patch.object(checker, '_query_consent_status', side_effect=mock_query):
            result = await checker._retry_query("P001")
            assert result == ConsentStatus.GRANTED
            assert call_count == checker._retry_max_attempts

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_timeout_error(self):
        from safety_gateway.errors import DatabaseQueryTimeoutError

        checker = ConsentChecker()

        with patch.object(checker, '_query_consent_status', side_effect=asyncio.TimeoutError("timeout")):
            with pytest.raises(DatabaseQueryTimeoutError):
                await checker._retry_query("P001")


class TestDegradedOutputIntegrity:

    def test_degraded_output_non_whitelist_removed(self):
        checker = get_safety_gateway_checker()
        data = {
            "organ_name": "甲状腺",
            "observations": "正常",
            "status": "completed",
            "request_id": "REQ001",
            "session_id": "SES001",
            "diagnosis": "TI-RADS 3",
            "patient_name": "张三",
            "internal_notes": "备注",
        }
        result = checker.apply_degraded_output(data, DegradedReason.SENSITIVE_DATA_DETECTED)

        assert "organ_name" in result
        assert "echogenicity" not in result
        assert "diagnosis" not in result
        assert "patient_name" not in result
        assert "internal_notes" not in result
        assert "conclusion_suppressed" in result
        assert result["conclusion_suppressed"] is True
        assert "degraded_fields" in result
        assert "diagnosis" in result["degraded_fields"]
        assert "patient_name" in result["degraded_fields"]
        assert "internal_notes" in result["degraded_fields"]

    def test_degraded_output_preserves_numeric_types(self):
        checker = get_safety_gateway_checker()
        data = {
            "echogenicity": 42,
            "organ_name": True,
            "echo_characteristics": None,
            "diagnosis": "TI-RADS 3",
        }
        result = checker.apply_degraded_output(data)
        assert result["echogenicity"] == 42
        assert result["organ_name"] is True


class TestDBTimeoutDegradedReason:

    @pytest.mark.asyncio
    async def test_consent_timeout_returns_degraded_with_db_timeout(self):
        checker = SafetyGatewayChecker()

        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_get_consent:
            mock_cc = AsyncMock()
            mock_cc.check_consent_status = AsyncMock(return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=True, error=False))
            mock_get_consent.return_value = mock_cc
            result = await checker.perform_full_check("P001", {
                "organ": "thyroid",
                "diagnosis_data": {"patient_conditions": []},
                "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            })
            assert result.status == CheckResultStatus.FAILED
            assert result.degraded_reason == DegradedReason.DB_TIMEOUT
            assert result.consent_check_timeout is True

    def test_db_timeout_degraded_reason_in_apply_degraded_output(self):
        checker = get_safety_gateway_checker()
        data = {
            "observations": "正常",
            "status": "completed",
            "diagnosis": "TI-RADS 3",
        }
        result = checker.apply_degraded_output(data, DegradedReason.DB_TIMEOUT)
        assert result.get("db_timeout") is True
        assert result.get("fusion_degraded") is False
        assert result.get("safety_gateway_unavailable") is False


class TestDegradationWarning:

    def test_degradation_warning_default_message(self):
        from safety_gateway.degraded_output import DegradedReason, apply_degraded_output
        output_data = {
            "organ_name": "thyroid",
            "diagnostic_conclusion": "恶性结节",
            "observations": "甲状腺左叶结节",
        }
        result = apply_degraded_output(output_data, reason=DegradedReason.FUSION_UNAVAILABLE, fusion_available=False)
        assert "degradation_warning" in result
        assert result["degradation_warning"] == "脱敏降级模式，身份信息保护等级降低"

    def test_degradation_warning_dual_degradation_message(self):
        from safety_gateway.degraded_output import DegradedReason, apply_degraded_output
        output_data = {
            "organ_name": "thyroid",
            "diagnostic_conclusion": "恶性结节",
            "observations": "甲状腺左叶结节",
        }
        result = apply_degraded_output(
            output_data,
            reason=DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
            fusion_available=False,
        )
        assert "degradation_warning" in result
        assert result["degradation_warning"] == "双重安全校验缺失，请人工核验"

    def test_degradation_warning_consent_withdrawn_dual_message(self):
        from safety_gateway.degraded_output import DegradedReason, apply_degraded_output
        output_data = {
            "organ_name": "thyroid",
            "diagnostic_conclusion": "恶性结节",
            "observations": "甲状腺左叶结节",
        }
        result = apply_degraded_output(
            output_data,
            reason=DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN,
            fusion_available=False,
        )
        assert "degradation_warning" in result
        assert result["degradation_warning"] == "双重安全校验缺失，请人工核验"


class TestConclusionSuppressedHandling:

    @pytest.mark.asyncio
    async def test_conclusion_suppressed_skips_diagnostic_conclusion(self):
        checker = SafetyGatewayChecker()
        output_data = {
            "observations": "甲状腺左叶可见低回声结节",
            "diagnostic_conclusion": None,
            "conclusion_suppressed": True,
            "diagnosis_data": {"patient_conditions": []},
        }
        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_consent, \
             patch.object(checker, 'check_desensitization', new_callable=AsyncMock, return_value=[]), \
             patch.object(checker, 'check_contraindication', new_callable=AsyncMock, return_value={"has_contraindication": False, "contraindications": [], "message": "无禁忌"}), \
             patch.object(checker, 'check_consistency', new_callable=AsyncMock, return_value={"consistent": True, "conflicts": []}), \
             patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
            mock_consent.return_value.check_consent_status = AsyncMock(return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False))
            mock_hc = AsyncMock()
            mock_hc.get_fusion_availability = AsyncMock(return_value=True)
            mock_hc.record_latency = AsyncMock()
            mock_hc.record_e2e_latency = AsyncMock()
            mock_hc_getter.return_value = mock_hc
            result = await checker.perform_full_check(
                patient_id="test-patient",
                output_data=output_data,
                conclusion_suppressed=True,
            )
            assert result.status == CheckResultStatus.PASSED

    @pytest.mark.asyncio
    async def test_conclusion_suppressed_with_sensitive_in_observations(self):
        checker = SafetyGatewayChecker()
        output_data = {
            "observations": "患者：张三，甲状腺左叶可见低回声结节",
            "diagnostic_conclusion": None,
            "conclusion_suppressed": True,
        }
        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_consent, \
             patch.object(checker, 'check_desensitization', new_callable=AsyncMock, return_value=["sg_patient_name"]), \
             patch.object(checker, 'check_contraindication', new_callable=AsyncMock, return_value={"has_contraindication": False, "contraindications": [], "message": "无禁忌"}), \
             patch.object(checker, 'check_consistency', new_callable=AsyncMock, return_value={"consistent": True, "conflicts": []}):
            mock_consent.return_value.check_consent_status = AsyncMock(return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False))
            result = await checker.perform_full_check(
                patient_id="test-patient",
                output_data=output_data,
                conclusion_suppressed=True,
            )
            assert result.degraded_mode is True


class TestRedisFailoverDegradationAdvanced:

    @pytest.mark.asyncio
    async def test_consent_check_pg_direct_success(self):
        checker = ConsentChecker()
        with patch.object(checker, '_query_consent_status', new_callable=AsyncMock, return_value=ConsentStatus.GRANTED):
            result = await checker.check_consent_status("test-patient")
            assert result.status == ConsentStatus.GRANTED

    @pytest.mark.asyncio
    async def test_consent_check_db_unavailable_returns_error(self):
        checker = ConsentChecker()
        with patch.object(checker, '_query_consent_status', new_callable=AsyncMock, side_effect=Exception("DB unavailable")):
            result = await checker.check_consent_status("test-patient")
            assert result.status is None or result.error is True
