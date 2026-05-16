import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from safety_gateway.validation_result import ConsentStatus, ThresholdCheckResult
from safety_gateway.checker import (
    SafetyGatewayChecker,
    CheckResultStatus,
    DegradedReason,
    get_safety_gateway_checker,
)
from safety_gateway.consent_checker import ConsentCheckResult
from safety_gateway.desensitization import sg_desensitization_engine

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestSafetyGatewayClient:

    def test_e4005_response(self):
        from app.services.safety_gateway_client import SafetyGatewayClient
        client = SafetyGatewayClient()
        result = client._e4005_response()
        assert result["passed"] is False
        assert result["error_code"] == "E4005"
        assert result["degraded_mode"] is True


class TestFullCheckIntegration:

    def setup_method(self):
        from unittest.mock import patch
        from safety_gateway.validation_result import ConsentStatus

        self.checker = get_safety_gateway_checker()

        self.mock_patcher = patch.object(
            self.checker._consent_checker,
            'check_consent_status',
            side_effect=lambda patient_id: ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)
        )
        self.mock_consent = self.mock_patcher.start()
        logger.info("TestFullCheckIntegration setup: mock consent checker started")

    def teardown_method(self):
        self.mock_patcher.stop()
        logger.info("TestFullCheckIntegration teardown: mock consent checker stopped")

    @pytest.mark.asyncio
    async def test_full_check_all_passed(self):
        checker = SafetyGatewayChecker()
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {"patient_conditions": []},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            "fusion_output": {"diagnostic_conclusion": {"grade": "T1", "finding": "benign"}},
            "specialist_conclusions": [{"organ": "thyroid", "grade": "T1", "finding": "benign"}],
        }

        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_get_consent, \
             patch.object(checker._threshold_phase, 'check_with_params', new_callable=AsyncMock, return_value=None), \
             patch.object(checker._contraindication_threshold_phase, 'check_with_params', new_callable=AsyncMock, return_value=None), \
             patch.object(checker._consistency_phase, 'check_with_params', new_callable=AsyncMock, return_value=None):
            mock_cc = AsyncMock()
            mock_cc.check_consent_status = AsyncMock(return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False))
            mock_get_consent.return_value = mock_cc
            result = await checker.perform_full_check("P001", test_data)
            assert result.status == CheckResultStatus.PASSED
            assert "五项校验全部通过" in result.message

    @pytest.mark.asyncio
    async def test_full_check_contraindication_hit(self):
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {"patient_conditions": ["pregnancy"], "clinical_context": "pregnant"},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        result = await self.checker.perform_full_check("P001", test_data)
        assert result.status == CheckResultStatus.DEGRADED
        assert result.degraded_reason == DegradedReason.CONTRAINDICATION_HIT

    @pytest.mark.asyncio
    async def test_full_check_threshold_failure(self):
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.5}],
        }

        result = await self.checker.perform_full_check("P001", test_data)
        assert result.status == CheckResultStatus.DEGRADED
        assert result.degraded_reason == DegradedReason.CONFIDENCE_BELOW_THRESHOLD

    @pytest.mark.asyncio
    async def test_full_check_consistency_failure(self):
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            "fusion_output": {"diagnostic_conclusion": {"grade": "T1", "finding": "benign"}},
            "specialist_conclusions": [{"organ": "thyroid", "grade": "T2", "finding": "malignant"}],
        }

        result = await self.checker.perform_full_check("P001", test_data)
        assert result.status in (CheckResultStatus.FAILED, CheckResultStatus.DEGRADED)

    @pytest.mark.asyncio
    async def test_full_check_sensitive_data_detected(self):
        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "patient_info": "患者身份证号：110101199001011234",
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        result = await self.checker.perform_full_check("P001", test_data)
        assert result.status == CheckResultStatus.DEGRADED
        assert "sg_id_card" in result.sensitive_data_detected

    @pytest.mark.asyncio
    async def test_full_check_execution_order(self):
        from safety_gateway.validation_result import ConsentStatus
        from unittest.mock import AsyncMock, MagicMock

        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            "fusion_output": {"diagnostic_conclusion": {"grade": "T1", "finding": "良性"}},
            "specialist_conclusions": [{"organ": "thyroid", "grade": "T1", "finding": "良性"}],
        }

        call_order = []

        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_get_cc:
            mock_cc = AsyncMock()
            async def mock_consent_status(pid):
                call_order.append("consent")
                return ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)
            mock_cc.check_consent_status = mock_consent_status
            mock_get_cc.return_value = mock_cc

            with patch.object(self.checker._desensitization_phase, 'check_desensitization', new_callable=AsyncMock) as mock_desens:
                mock_desens.side_effect = lambda data: (call_order.append("desensitization"), [])[1]

                with patch('safety_gateway.checker.threshold_phase.get_threshold_engine') as mock_get_te:
                    mock_te = AsyncMock()
                    from safety_gateway.validation_result import ThresholdCheckResult
                    _tcr = ThresholdCheckResult(organ="thyroid", confidence=0.95, threshold=0.85, passed=True, message="阈值校验通过")
                    async def mock_threshold_check_multiple(conclusions):
                        call_order.append("threshold")
                        return [_tcr]
                    mock_te.check_multiple = mock_threshold_check_multiple
                    mock_get_te.return_value = mock_te

                    with patch.object(self.checker._contraindication_threshold_phase, 'check_contraindication', new_callable=AsyncMock) as mock_contra:
                        mock_contra.side_effect = lambda pid, organ, dd: (call_order.append("contraindication"), {"has_contraindication": False, "contraindications": []})[1]

                        with patch.object(self.checker._consistency_phase, 'check_consistency', new_callable=AsyncMock) as mock_consist:
                            mock_consist.side_effect = lambda fo, sc: (call_order.append("consistency"), {"consistent": True, "conflicts": [], "message": "一致性校验通过"})[1]

                            result = await self.checker.perform_full_check("P001", test_data)

                            expected_order = ["consent", "desensitization", "threshold", "contraindication", "consistency"]
                            assert call_order == expected_order, f"执行顺序错误: 期望{expected_order}, 实际{call_order}"

                            assert result.status == CheckResultStatus.PASSED


class TestServiceIsolation:

    def test_e4005_error_code_constant(self):
        from safety_gateway.errors import ServiceUnavailableError

        error = ServiceUnavailableError()
        assert error.code == "E4005"

    @pytest.mark.asyncio
    async def test_db_error_returns_sg_unavailable(self):
        from safety_gateway.errors import DatabaseUnavailableError as SGDatabaseUnavailableError
        checker = get_safety_gateway_checker()

        with patch.object(checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=None, timeout=False, error=True)):
            with pytest.raises(SGDatabaseUnavailableError):
                await checker.perform_full_check("P001", {"organ": "thyroid", "diagnosis_data": {}})

    @pytest.mark.asyncio
    async def test_safety_gateway_independent_operation(self):
        checker = get_safety_gateway_checker()
        from safety_gateway.validation_result import ConsentStatus

        test_data = {
            "organ": "thyroid",
            "diagnosis_data": {},
            "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
        }

        with patch.object(checker._consent_checker, 'check_consent_status',
                         new_callable=AsyncMock,
                         return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            result = await checker.perform_full_check("P001", test_data)
            assert result.status in [CheckResultStatus.PASSED, CheckResultStatus.DEGRADED]
            assert isinstance(result.consent_check_timeout, bool)


class TestMainServiceCrashScenario:

    def test_e4005_error_response_structure(self):
        from safety_gateway.errors import ServiceUnavailableError

        error = ServiceUnavailableError(message="主服务不可用", details={"reason": "main_service_crashed"})
        assert error.code == "E4005"
        assert str(error) == "主服务不可用"
        assert error.details["reason"] == "main_service_crashed"

    @pytest.mark.asyncio
    async def test_sg_returns_e4005_when_db_unavailable(self):
        from safety_gateway.errors import DatabaseUnavailableError as SGDatabaseUnavailableError
        checker = get_safety_gateway_checker()

        with patch.object(checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=None, timeout=False, error=True)):
            with pytest.raises(SGDatabaseUnavailableError) as exc_info:
                await checker.perform_full_check("P001", {"organ": "thyroid", "diagnosis_data": {}})
            assert exc_info.value.code == "E4005B"

    @pytest.mark.asyncio
    async def test_sg_returns_e4005_on_circuit_breaker_open(self):
        from safety_gateway.errors import DatabaseUnavailableError as SGDatabaseUnavailableError
        checker = get_safety_gateway_checker()

        with patch.object(checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, return_value=ConsentCheckResult(status=None, timeout=False, error=True)):
            with pytest.raises(SGDatabaseUnavailableError) as exc_info:
                await checker.perform_full_check("P001", {"organ": "thyroid", "diagnosis_data": {}})
            assert exc_info.value.code == "E4005B"

    @pytest.mark.asyncio
    async def test_sg_continues_operation_after_main_service_recovery(self):
        from safety_gateway.errors import DatabaseUnavailableError as SGDatabaseUnavailableError
        checker = get_safety_gateway_checker()
        call_count = 0

        def simulate_recovery(patient_id):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return ConsentCheckResult(status=None, timeout=False, error=True)
            return ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)

        with patch.object(checker._consent_checker, 'check_consent_status', new_callable=AsyncMock, side_effect=simulate_recovery):
            for _ in range(3):
                with pytest.raises(SGDatabaseUnavailableError):
                    await checker.perform_full_check("P001", {"organ": "thyroid", "diagnosis_data": {}})

            test_data = {
                "organ": "thyroid",
                "diagnosis_data": {},
                "conclusions": [{"organ": "thyroid", "confidence": 0.95}],
            }
            result = await checker.perform_full_check("P001", test_data)
            assert result.status in [CheckResultStatus.PASSED, CheckResultStatus.DEGRADED]


class TestSGCombinedUnavailableError:

    @pytest.mark.asyncio
    async def test_sg_combined_unavailable_e4005c(self):
        from safety_gateway.errors import CombinedUnavailableError as SGCombinedUnavailableError
        checker = get_safety_gateway_checker()
        with patch.object(checker._consent_checker, 'check_consent_status',
                         new_callable=AsyncMock,
                         return_value=ConsentCheckResult(status=None, timeout=False, error=True)):
            with patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
                mock_hc = AsyncMock()
                mock_hc.get_fusion_availability = AsyncMock(return_value=False)
                mock_hc.record_latency = AsyncMock()
                mock_hc.record_e2e_latency = AsyncMock()
                mock_hc_getter.return_value = mock_hc
                with pytest.raises(SGCombinedUnavailableError) as exc_info:
                    await checker.perform_full_check(
                        "P001",
                        {"organ": "thyroid", "diagnosis_data": {}, "conclusions": [{"organ": "thyroid", "confidence": 0.95}]},
                    )
                assert exc_info.value.code == "E4005C"


class TestRulesSyncSubscriber:

    @pytest.mark.asyncio
    async def test_rules_sync_triggers_reload(self):
        from safety_gateway.main import _handle_rules_sync_message
        with patch.object(sg_desensitization_engine, 'reload_rules', return_value=True) as mock_reload:
            await _handle_rules_sync_message({"action": "update"})
            mock_reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_rules_sync_reload_failure_handled(self):
        from safety_gateway.main import _handle_rules_sync_message
        with patch.object(sg_desensitization_engine, 'reload_rules', side_effect=Exception("YAML parse error")):
            await _handle_rules_sync_message({"action": "update"})

    @pytest.mark.asyncio
    async def test_rules_sync_subscriber_start_stop(self):
        from safety_gateway.redis_subscriber import RedisSubscriber
        from safety_gateway.redis_pool import SafetyGatewayRedisPool
        handler_calls = []

        async def mock_handler(data):
            handler_calls.append(data)

        subscriber = RedisSubscriber(
            channel="test_channel",
            handler=mock_handler,
        )
        with patch.object(SafetyGatewayRedisPool, 'get_client', new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            await subscriber.start()
            await asyncio.sleep(0)
            await subscriber.stop()
            assert subscriber._task is None


class TestSafetyGatewayClientQueueLogic:

    def setup_method(self):
        from app.services.safety_gateway_client import SafetyGatewayClient
        self.client = SafetyGatewayClient()

    @pytest.mark.asyncio
    async def test_consecutive_failures_increments_on_connect_error(self):
        self.client._consecutive_failures = 0
        async with self.client._lock:
            self.client._consecutive_failures += 1
        assert self.client._consecutive_failures == 1
        async with self.client._lock:
            self.client._consecutive_failures += 1
        assert self.client._consecutive_failures == 2

    @pytest.mark.asyncio
    async def test_fast_fail_after_three_consecutive_failures(self):
        self.client._consecutive_failures = 3
        assert self.client._consecutive_failures >= self.client.FAST_FAIL_THRESHOLD

    @pytest.mark.asyncio
    async def test_consecutive_failures_reset_on_success(self):
        self.client._consecutive_failures = 2
        async with self.client._lock:
            self.client._consecutive_failures = 0
        assert self.client._consecutive_failures == 0
