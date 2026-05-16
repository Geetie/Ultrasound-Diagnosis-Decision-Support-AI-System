import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.threshold import get_threshold_engine
from safety_gateway.validation_result import ThresholdCheckResult, ConsentStatus
from safety_gateway.checker import (
    SafetyGatewayChecker,
    CheckResultStatus,
    DegradedReason,
    CheckResult,
    get_safety_gateway_checker,
)
from safety_gateway.consent_checker import ConsentCheckResult

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestSafetyGatewayChecker:

    def setup_method(self):
        self.checker = SafetyGatewayChecker()

    def test_default_whitelist_fields(self):
        fields = self.checker.get_whitelist_fields()
        assert "lesion_size_measurements" in fields
        assert "organ_name" in fields
        assert "echogenicity" in fields
        assert "vascularity" in fields

    def test_get_whitelist_fields(self):
        fields = self.checker.get_whitelist_fields()
        assert "lesion_size_measurements" in fields

    @pytest.mark.asyncio
    async def test_check_desensitization_no_sensitive(self):
        data = {"observations": "甲状腺正常", "status": "completed"}
        result = await self.checker.check_desensitization(data)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_check_desensitization_with_phone(self):
        data = {"contact": "13812345678"}
        result = await self.checker.check_desensitization(data)
        assert "sg_phone" in result

    @pytest.mark.asyncio
    async def test_check_desensitization_with_nested_dict(self):
        data = {"patient": {"phone": "13812345678"}}
        result = await self.checker.check_desensitization(data)
        assert "sg_phone" in result

    @pytest.mark.asyncio
    async def test_check_desensitization_with_list(self):
        data = {"contacts": ["13812345678"]}
        result = await self.checker.check_desensitization(data)
        assert "sg_phone" in result

    @pytest.mark.asyncio
    async def test_check_contraindication_hit(self):
        result = await self.checker.check_contraindication(
            patient_id="P001",
            organ="thyroid",
            diagnosis_data={"patient_conditions": ["pregnancy"]},
        )
        assert result["has_contraindication"] is True
        assert len(result["contraindications"]) > 0

    @pytest.mark.asyncio
    async def test_check_contraindication_no_hit(self):
        result = await self.checker.check_contraindication(
            patient_id="P001",
            organ="thyroid",
            diagnosis_data={"patient_conditions": ["none"]},
        )
        assert result["has_contraindication"] is False

    @pytest.mark.asyncio
    async def test_check_consistency_single_conclusion(self):
        result = await self.checker.check_consistency(
            fusion_output={},
            specialist_conclusions=[{"organ": "thyroid", "grade": "TI-RADS 3"}],
        )
        assert result["consistent"] is True

    @pytest.mark.asyncio
    async def test_check_consistency_consistent(self):
        result = await self.checker.check_consistency(
            fusion_output={"diagnostic_conclusion": {"grade": "TI-RADS 3", "finding": "良性"}},
            specialist_conclusions=[
                {"organ": "thyroid", "grade": "TI-RADS 3", "finding": "良性"},
            ],
        )
        assert result["consistent"] is True

    @pytest.mark.asyncio
    async def test_check_consistency_grade_mismatch(self):
        result = await self.checker.check_consistency(
            fusion_output={"diagnostic_conclusion": {"grade": "TI-RADS 4", "finding": "可疑"}},
            specialist_conclusions=[
                {"organ": "thyroid", "grade": "TI-RADS 3", "finding": "可疑"},
            ],
        )
        assert result["consistent"] is False
        assert any(c["type"] == "grade_mismatch" for c in result["conflicts"])

    @pytest.mark.asyncio
    async def test_check_consistency_finding_mismatch(self):
        result = await self.checker.check_consistency(
            fusion_output={"diagnostic_conclusion": {"grade": "TI-RADS 3", "finding": "良性"}},
            specialist_conclusions=[
                {"organ": "thyroid", "grade": "TI-RADS 3", "finding": "可疑"},
            ],
        )
        assert result["consistent"] is False
        assert any(c["type"] == "finding_mismatch" for c in result["conflicts"])

    def test_apply_degraded_output_sensitive_data(self):
        data = {
            "observations": "甲状腺正常",
            "diagnosis": "TI-RADS 3",
            "status": "completed",
            "patient_name": "张三",
        }
        result = self.checker.apply_degraded_output(data, DegradedReason.SENSITIVE_DATA_DETECTED)
        assert "conclusion_suppressed" in result
        assert result["conclusion_suppressed"] is True
        assert "degraded_fields" in result
        assert "diagnosis" in result["degraded_fields"]
        assert "patient_name" in result["degraded_fields"]
        assert result["sg_output_degraded"] is True
        assert result["fusion_degraded"] is False
        assert result["safety_gateway_unavailable"] is False

    def test_apply_degraded_output_fusion_unavailable_sensitive_detected(self):
        data = {"observations": "正常", "diagnosis": "TI-RADS 3"}
        result = self.checker.apply_degraded_output(data, DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED)
        assert result["safety_gateway_unavailable"] is False
        assert result["fusion_degraded"] is True
        assert result["fusion_unavailable_sensitive_detected"] is True
        assert result["error_code"] == "E4005A1"

    def test_apply_degraded_output_dual_degradation(self):
        data = {"observations": "正常", "diagnostic_conclusion": "恶性"}
        result = self.checker.apply_degraded_output(data, DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE)
        assert result["conclusion_suppressed"] is True
        assert result["fusion_degraded"] is True
        assert result["safety_gateway_unavailable"] is True

    def test_apply_degraded_output_diagnostic_conclusion_explicitly_filtered(self):
        data = {"observations": "正常", "diagnostic_conclusion": "TI-RADS 4", "organ_name": "甲状腺"}
        result = self.checker.apply_degraded_output(data, DegradedReason.CONTRAINDICATION_HIT)
        assert "diagnostic_conclusion" not in result
        assert "organ_name" in result

    def test_apply_degraded_output_db_timeout(self):
        data = {"observations": "正常", "diagnosis": "TI-RADS 3"}
        result = self.checker.apply_degraded_output(data, DegradedReason.DB_TIMEOUT)
        assert result.get("db_timeout") is True
        assert result["safety_gateway_unavailable"] is False

    def test_apply_degraded_output_contraindication(self):
        data = {"observations": "正常", "diagnosis": "TI-RADS 3"}
        result = self.checker.apply_degraded_output(data, DegradedReason.CONTRAINDICATION_HIT)
        assert result.get("contraindication_hit") is True

    def test_apply_degraded_output_confidence_below(self):
        data = {"observations": "正常", "diagnosis": "TI-RADS 3"}
        result = self.checker.apply_degraded_output(data, DegradedReason.CONFIDENCE_BELOW_THRESHOLD)
        assert result.get("confidence_below_threshold") is True

    def test_apply_degraded_output_whitelist_only(self):
        data = {
            "anatomical_location": "左叶",
            "organ_name": "甲状腺",
            "echogenicity": "等回声",
            "diagnosis": "TI-RADS 3",
        }
        result = self.checker.apply_degraded_output(data)
        assert "anatomical_location" in result
        assert "organ_name" in result
        assert "echogenicity" in result
        assert "diagnosis" not in result

    def test_apply_degraded_output_string_desensitization(self):
        data = {
            "organ_name": "联系电话13812345678",
            "diagnosis": "TI-RADS 3",
        }
        result = self.checker.apply_degraded_output(data)
        assert "13812345678" not in str(result.get("organ_name", ""))

    def test_apply_degraded_output_dict_desensitization(self):
        data = {
            "organ_name": {"note": "电话13812345678"},
            "diagnosis": "TI-RADS 3",
        }
        result = self.checker.apply_degraded_output(data)
        obs = result.get("organ_name", {})
        assert "13812345678" not in str(obs)

    def test_apply_degraded_output_preserves_non_string(self):
        data = {
            "echogenicity": 42,
            "organ_name": True,
            "echo_characteristics": 3.14,
        }
        result = self.checker.apply_degraded_output(data)
        assert result["echogenicity"] == 42
        assert result["organ_name"] is True

    @pytest.mark.asyncio
    async def test_perform_full_check_consent_denied(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock:
            mock.return_value = ConsentCheckResult(status=ConsentStatus.DENIED, timeout=False, error=False)
            result = await self.checker.perform_full_check("P001", {"observations": "test"}, consent_status_at_input="denied")
            assert result.status == CheckResultStatus.FAILED
            assert result.consent_status == ConsentStatus.DENIED

    @pytest.mark.asyncio
    async def test_perform_full_check_consent_withdrawn(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock:
            mock.return_value = ConsentCheckResult(status=ConsentStatus.WITHDRAWN, timeout=False, error=False)
            result = await self.checker.perform_full_check("P001", {"observations": "test"}, consent_status_at_input="withdrawn")
            assert result.status == CheckResultStatus.FAILED
            assert result.consent_status == ConsentStatus.WITHDRAWN

    @pytest.mark.asyncio
    async def test_perform_full_check_consent_granted_no_sensitive(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock_consent, \
             patch.object(self.checker, 'check_desensitization', new_callable=AsyncMock) as mock_desens, \
             patch.object(self.checker, 'check_contraindication', new_callable=AsyncMock) as mock_contra, \
             patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_hc_getter:
            mock_consent.return_value = ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)
            mock_desens.return_value = []
            mock_contra.return_value = {"has_contraindication": False, "contraindications": [], "message": ""}
            mock_hc = AsyncMock()
            mock_hc.get_fusion_availability = AsyncMock(return_value=True)
            mock_hc.record_latency = AsyncMock()
            mock_hc.record_e2e_latency = AsyncMock()
            mock_hc_getter.return_value = mock_hc
            result = await self.checker.perform_full_check("P001", {"observations": "test", "diagnosis_data": {"patient_conditions": []}})
            assert result.status == CheckResultStatus.PASSED

    @pytest.mark.asyncio
    async def test_perform_full_check_consent_granted_with_sensitive(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock_consent, \
             patch.object(self.checker, 'check_desensitization', new_callable=AsyncMock) as mock_desens:
            mock_consent.return_value = ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)
            mock_desens.return_value = ["phone"]
            result = await self.checker.perform_full_check("P001", {"phone": "13812345678"})
            assert result.status == CheckResultStatus.DEGRADED
            assert result.degraded_mode is True
            assert result.degraded_reason == DegradedReason.SENSITIVE_DATA_DETECTED

    @pytest.mark.asyncio
    async def test_perform_full_check_db_error(self):
        from safety_gateway.errors import DatabaseUnavailableError as SGDatabaseUnavailableError
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock:
            mock.return_value = ConsentCheckResult(status=None, timeout=False, error=True)
            with pytest.raises(SGDatabaseUnavailableError):
                await self.checker.perform_full_check("P001", {"observations": "test"})

    @pytest.mark.asyncio
    async def test_perform_full_check_consent_timeout(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock:
            mock.return_value = ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=True, error=False)
            result = await self.checker.perform_full_check("P001", {"observations": "test"})
            assert result.consent_check_timeout is True

    @pytest.mark.asyncio
    async def test_perform_full_check_none_consent(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock:
            mock.return_value = ConsentCheckResult(status=None, timeout=False, error=False)
            result = await self.checker.perform_full_check("P001", {"observations": "test"})
            assert result.status == CheckResultStatus.FAILED
            assert result.degraded_mode is True
            assert result.degraded_reason == DegradedReason.CONSENT_STATUS_UNKNOWN

    @pytest.mark.asyncio
    async def test_perform_full_check_elapsed_ms(self):
        with patch.object(self.checker._consent_checker, 'check_consent_status', new_callable=AsyncMock) as mock:
            mock.return_value = ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)
            result = await self.checker.perform_full_check("P001", {"observations": "test"})
            assert result.elapsed_ms >= 0


class TestCheckResultStatus:

    def test_status_values(self):
        assert CheckResultStatus.PASSED == "passed"
        assert CheckResultStatus.FAILED == "failed"
        assert CheckResultStatus.DEGRADED == "degraded"


class TestDegradedReason:

    def test_reason_values(self):
        assert DegradedReason.SENSITIVE_DATA_DETECTED == "sensitive_data_detected"
        assert DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED == "fusion_unavailable_sensitive_detected"
        assert DegradedReason.DB_TIMEOUT == "db_timeout"
        assert DegradedReason.CONTRAINDICATION_HIT == "contraindication_hit"
        assert DegradedReason.CONFIDENCE_BELOW_THRESHOLD == "confidence_below_threshold"


class TestCheckResult:

    def test_check_result_defaults(self):
        result = CheckResult(status=CheckResultStatus.PASSED, message="通过")
        assert result.sensitive_data_detected == []
        assert result.degraded_mode is False
        assert result.elapsed_ms == 0.0

    def test_check_result_degraded(self):
        result = CheckResult(
            status=CheckResultStatus.DEGRADED,
            message="降级",
            degraded_mode=True,
            degraded_reason=DegradedReason.SENSITIVE_DATA_DETECTED,
        )
        assert result.degraded_mode is True
        assert result.degraded_reason == DegradedReason.SENSITIVE_DATA_DETECTED


class TestSingletonGetters:

    def test_get_contraindication_engine(self):
        from safety_gateway.contraindication import get_contraindication_engine
        engine1 = get_contraindication_engine()
        engine2 = get_contraindication_engine()
        assert engine1 is engine2

    def test_get_threshold_engine(self):
        engine1 = get_threshold_engine()
        engine2 = get_threshold_engine()
        assert engine1 is engine2

    def test_get_safety_gateway_checker(self):
        checker1 = get_safety_gateway_checker()
        checker2 = get_safety_gateway_checker()
        assert checker1 is checker2


class TestSafetyGatewayErrors:

    def test_error_hierarchy(self):
        from safety_gateway.errors import (
            SafetyGatewayError,
            DatabaseError,
            DatabaseConnectionError,
            ConsentError,
            ValidationError,
            CircuitBreakerError,
        )

        assert issubclass(DatabaseError, SafetyGatewayError)
        assert issubclass(DatabaseConnectionError, DatabaseError)
        assert issubclass(ConsentError, SafetyGatewayError)
        assert issubclass(ValidationError, SafetyGatewayError)
        assert issubclass(CircuitBreakerError, SafetyGatewayError)

    def test_error_codes(self):
        from safety_gateway.errors import (
            DatabaseConnectionError,
            DatabaseQueryTimeoutError,
            ConsentCheckError,
            SensitiveDataError,
            ContraindicationError,
            ThresholdError,
            CircuitBreakerError,
        )

        assert DatabaseConnectionError.code == "SG_DB_CONNECTION"
        assert DatabaseQueryTimeoutError.code == "SG_DB_TIMEOUT"
        assert ConsentCheckError.code == "SG_CONSENT_CHECK"
        assert SensitiveDataError.code == "SG_SENSITIVE_DATA"
        assert ContraindicationError.code == "SG_CONTRAINDICATION"
        assert ThresholdError.code == "SG_THRESHOLD"
        assert CircuitBreakerError.code == "SG_CIRCUIT_BREAKER"

    def test_error_to_dict(self):
        from safety_gateway.errors import DatabaseConnectionError

        error = DatabaseConnectionError("连接失败", {"host": "localhost"})
        result = error.to_dict()
        assert result["code"] == "E4005B"
        assert result["message"] == "连接失败"
        assert result["details"] == {"host": "localhost"}

    def test_error_code_map(self):
        from safety_gateway.errors import DatabaseConnectionError, SafetyGatewayError
        assert DatabaseConnectionError.code == "SG_DB_CONNECTION"
        assert SafetyGatewayError.code == "SG_ERROR"


class TestRecursionDepthLimit:

    @pytest.mark.asyncio
    async def test_recursion_depth_limit(self):
        checker = get_safety_gateway_checker()

        nested_data = {"level_0": "safe"}
        current = nested_data
        for i in range(15):
            current[f"level_{i+1}"] = {}
            current = current[f"level_{i+1}"]
        current["phone"] = "13812345678"

        result = await checker.check_desensitization(nested_data)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_nested_list_in_dict(self):
        checker = get_safety_gateway_checker()

        data = {"contacts": [["13812345678", "13987654321"]]}
        result = await checker.check_desensitization(data)
        assert "sg_phone" in result


class TestBoundaryConditions:

    def test_liver_exact_threshold(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = engine._check_sync("liver", 0.88)
        assert result.passed is True

    def test_liver_below_threshold(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = engine._check_sync("liver", 0.87)
        assert result.passed is False

    def test_cardiac_exact_threshold(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = engine._check_sync("cardiac", 0.92)
        assert result.passed is True

    def test_cardiac_below_threshold(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = engine._check_sync("cardiac", 0.91)
        assert result.passed is False

    def test_desensitize_empty_dict(self):
        result = sg_desensitization_engine.desensitize_dict({})
        assert result == {}

    @pytest.mark.asyncio
    async def test_check_desensitization_empty_dict(self):
        checker = get_safety_gateway_checker()
        result = await checker.check_desensitization({})
        assert result == []

    def test_desensitize_long_string(self):
        long_text = "a" * 10000 + "13812345678" + "b" * 10000
        result = sg_desensitization_engine.desensitize(long_text)
        assert "13812345678" not in result

    def test_apply_degraded_output_none_value(self):
        checker = get_safety_gateway_checker()
        data = {
            "observations": None,
            "status": "completed",
            "diagnosis": "TI-RADS 3",
        }
        result = checker.apply_degraded_output(data)
        assert result["observations"] is None
        assert "diagnosis" not in result


class TestBoundaryConditionsExtended:

    def test_confidence_zero(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = engine._check_sync("thyroid", 0.0)
        assert result.passed is False

    def test_confidence_one(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = engine._check_sync("thyroid", 1.0)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_empty_conclusions_list(self):
        checker = get_safety_gateway_checker()
        with patch.object(checker._consent_checker, 'check_consent_status',
                         new_callable=AsyncMock,
                         return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False)):
            result = await checker.perform_full_check("P001", {
                "organ": "thyroid",
                "diagnosis_data": {},
                "conclusions": [],
            })
            assert result.status in [CheckResultStatus.PASSED, CheckResultStatus.DEGRADED]

    @pytest.mark.asyncio
    async def test_unknown_organ_threshold(self):
        engine = get_threshold_engine()
        engine._load_sync_fallback()
        result = engine._check_sync("unknown_organ", 0.95)
        assert result.passed is True


class TestNegationPrefixExpansion:

    def test_deny_prefix_detects_negation(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase.__new__(ConsistencyPhase)
        assert phase._is_negated("患者否认恶性病史", "恶性") is True

    def test_no_evidence_prefix_detects_negation(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase.__new__(ConsistencyPhase)
        assert phase._is_negated("无证据提示恶性", "恶性") is True

    def test_not_support_prefix_detects_negation(self):
        from safety_gateway.checker.consistency_phase import ConsistencyPhase
        phase = ConsistencyPhase.__new__(ConsistencyPhase)
        assert phase._is_negated("不支持恶性诊断", "恶性") is True


class TestFilterForbiddenTerms:

    def test_multiple_forbidden_terms_in_one_string(self):
        checker = get_safety_gateway_checker()
        result = checker._filter_forbidden_terms("TI-RADS 4类恶性结节")
        assert "***" in result
        assert "TI-RADS" not in result
        assert "恶性" not in result

    def test_nested_dict_filtering(self):
        checker = get_safety_gateway_checker()
        data = {"key1": "TI-RADS 3类", "key2": {"nested": "恶性可能"}}
        result = checker._filter_forbidden_terms(data)
        assert "***" in result["key1"]
        assert "***" in result["key2"]["nested"]

    def test_recursion_depth_limit(self):
        checker = get_safety_gateway_checker()
        data = {"level": "test"}
        for _ in range(15):
            data = {"nested": data}
        result = checker._filter_forbidden_terms(data)
        assert result is not None


class TestHallucinationWarning:

    @pytest.mark.asyncio
    async def test_severe_hallucination_sets_warning(self):
        checker = SafetyGatewayChecker()
        output_data = {
            "observations": "甲状腺左叶可见低回声结节",
            "diagnostic_conclusion": "甲状腺左叶结节，TI-RADS 3类",
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
                hallucination_severity="severe",
            )
            assert result.hallucination_warning is True
            assert "幻觉" in result.message

    @pytest.mark.asyncio
    async def test_no_hallucination_no_warning(self):
        checker = SafetyGatewayChecker()
        output_data = {
            "observations": "甲状腺左叶可见低回声结节",
            "diagnostic_conclusion": "甲状腺左叶结节，TI-RADS 3类",
        }
        with patch('safety_gateway.checker.consent_phase.get_consent_checker') as mock_consent, \
             patch.object(checker, 'check_desensitization', new_callable=AsyncMock, return_value=[]), \
             patch.object(checker, 'check_contraindication', new_callable=AsyncMock, return_value={"has_contraindication": False, "contraindications": [], "message": "无禁忌"}), \
             patch.object(checker, 'check_consistency', new_callable=AsyncMock, return_value={"consistent": True, "conflicts": []}):
            mock_consent.return_value.check_consent_status = AsyncMock(return_value=ConsentCheckResult(status=ConsentStatus.GRANTED, timeout=False, error=False))
            result = await checker.perform_full_check(
                patient_id="test-patient",
                output_data=output_data,
            )
            assert result.hallucination_warning is False


class TestPortConflictDetection:

    def test_port_8000_conflict_detected(self):
        from safety_gateway.sg_config import SGSettings
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="conflicts"):
            SGSettings(safety_gateway_port=8000)

    def test_port_9001_no_conflict(self):
        from safety_gateway.sg_config import SGSettings
        settings = SGSettings(safety_gateway_port=9001)
        assert settings.safety_gateway_port == 9001


class TestDegradedReasonSemantics:

    def test_sg_unavailable_removed_from_check_result_status(self):
        assert not hasattr(CheckResultStatus, 'SG_UNAVAILABLE')

    def test_sg_unavailable_removed_from_degraded_reason(self):
        assert not hasattr(DegradedReason, 'SG_UNAVAILABLE')

    def test_fusion_unavailable_sensitive_detected_exists(self):
        assert hasattr(DegradedReason, 'FUSION_UNAVAILABLE_SENSITIVE_DETECTED')

    def test_desensitization_degraded_sensitive_detected_exists(self):
        assert hasattr(DegradedReason, 'DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED')


class TestE4005ASubCodeSplit:

    def test_desensitization_safety_violation_error_code(self):
        from safety_gateway.errors import DesensitizationSafetyViolationError
        exc = DesensitizationSafetyViolationError()
        assert exc.code == "E4005A1"

    def test_consent_safety_violation_error_code(self):
        from safety_gateway.errors import ConsentSafetyViolationError
        exc = ConsentSafetyViolationError()
        assert exc.code == "E4005A2"

    def test_error_code_map_includes_both(self):
        from safety_gateway.errors import DesensitizationSafetyViolationError, ConsentSafetyViolationError
        assert DesensitizationSafetyViolationError.code == "E4005A1"
        assert ConsentSafetyViolationError.code == "E4005A2"
