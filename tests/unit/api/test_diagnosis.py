from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.api.v1.diagnosis import (
    IDEMPOTENCY_TTL_SECONDS,
    RESULT_CACHE_TTL_SECONDS,
    RESULT_TIMEOUT_SECONDS,
    _generate_request_id,
    _push_diagnosis_result,
)
from app.api.v1.errors import (
    PatientConsentWithdrawnException,
    PatientNotConsentedException,
    SafetyGatewayUnavailableException,
)
from app.config.settings import settings as app_settings
from app.models.schemas import (
    ConsentStatus,
    ConsistencyFlag,
    DesensitizationStatus,
    DiagnosisRequest,
    DiagnosisResult,
    FusionStatus,
    ResponseTier,
    RetryStrategy,
)

pytestmark = pytest.mark.track_api


class TestDiagnosisRequestIdGeneration:
    def test_format_prefix(self):
        rid = _generate_request_id()
        assert rid.startswith("req_")

    def test_format_date_component(self):
        rid = _generate_request_id()
        parts = rid.split("_")
        assert len(parts[1]) == 8
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        assert parts[1] == today

    def test_uniqueness(self):
        ids = {_generate_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestDiagnosisPhase0AMockConstants:
    def test_mock_processing_time(self):
        assert app_settings.phase0a_mock_processing_time_ms == 2500

    def test_mock_model_version(self):
        assert app_settings.phase0a_mock_model_version == "thyroid_v1.0.0"

    def test_mock_kb_version(self):
        assert app_settings.phase0a_mock_kb_version == "kb_v1.0.0"

    def test_idempotency_ttl(self):
        assert IDEMPOTENCY_TTL_SECONDS == 600

    def test_result_cache_ttl(self):
        assert RESULT_CACHE_TTL_SECONDS == 600

    def test_result_timeout(self):
        """P1-13：阈值不再是硬编码 30s。

        原断言 ``== 30`` 是在**钉住一个 bug**：真实诊断管线要 90s 以上
        （dispatcher + fusion 的 LLM 串行链 + 冷启动），30s 会把正常在跑的
        诊断误报成 timeout，前端据此停止轮询。

        现在钉住的是正确语义：可配置 + 不低于真实管线下界。
        """
        from app.config.settings import settings

        assert settings.result_read_timeout_seconds == RESULT_TIMEOUT_SECONDS
        assert RESULT_TIMEOUT_SECONDS >= 90, (
            f"阈值 {RESULT_TIMEOUT_SECONDS}s 低于真实管线耗时，会误报 timeout"
        )


class TestDiagnosisRequestModel:
    def test_minimal_request(self):
        req = DiagnosisRequest(
            session_id="sess_001", target_organ="thyroid", image_ids=[]
        )
        assert req.session_id == "sess_001"
        assert req.target_organ == "thyroid"
        assert req.response_tier == ResponseTier.FULL
        assert req.retry is False
        assert req.retry_strategy is None
        assert req.idempotency_key is None

    def test_full_request(self):
        req = DiagnosisRequest(
            session_id="sess_001",
            target_organ="thyroid",
            image_ids=["img_001"],
            response_tier=ResponseTier.REALTIME,
            retry=True,
            retry_strategy=RetryStrategy.GENERATION_ONLY,
            idempotency_key="key_123",
        )
        assert req.response_tier == ResponseTier.REALTIME
        assert req.retry is True
        assert req.retry_strategy == RetryStrategy.GENERATION_ONLY
        assert req.idempotency_key == "key_123"


class TestDiagnosisResultModel:
    def test_result_fields(self):
        result = DiagnosisResult(
            request_id="req_001",
            status="completed",
            desensitization_status=DesensitizationStatus.FULL,
            consent_status_at_output=ConsentStatus.GRANTED,
            fusion_status=FusionStatus.NORMAL,
            consistency_flag=ConsistencyFlag.CONSISTENT,
            diagnosis={"organ": "thyroid", "findings": "normal"},
            requires_manual_review=False,
            processing_time_ms=2500,
        )
        assert result.request_id == "req_001"
        assert result.status == "completed"
        assert result.desensitization_status == DesensitizationStatus.FULL
        assert result.consent_status_at_output == ConsentStatus.GRANTED
        assert result.fusion_status == FusionStatus.NORMAL
        assert result.hallucination_severity is None
        assert result.version_compatibility == "compatible"

    def test_degraded_result(self):
        result = DiagnosisResult(
            request_id="req_002",
            status="completed",
            desensitization_status=DesensitizationStatus.DEGRADED,
            consent_status_at_output=ConsentStatus.DENIED,
            fusion_status=FusionStatus.BYPASSED,
            diagnosis={},
            requires_manual_review=True,
            review_reason="safety_check_failed",
            processing_time_ms=1000,
        )
        assert result.desensitization_status == DesensitizationStatus.DEGRADED
        assert result.requires_manual_review is True


class TestDiagnosisConsentFlow:
    def test_consent_not_granted_raises(self):
        exc = PatientNotConsentedException()
        assert exc.error_code == "E1008"
        assert exc.status_code == 403

    def test_consent_withdrawn_raises(self):
        exc = PatientConsentWithdrawnException()
        assert exc.error_code == "E1010"
        assert exc.status_code == 403
        assert exc.suggestion is not None


class TestDiagnosisSafetyGateway:
    def test_safety_gateway_unavailable(self):
        exc = SafetyGatewayUnavailableException()
        assert exc.error_code == "E4005"
        assert exc.status_code == 503
        assert exc.retryable is True


class TestPushDiagnosisResult:
    @pytest.mark.asyncio
    async def test_push_success(self):
        mock_result = Mock()
        mock_result.session_id = "sess_001"
        mock_result.result_pushed = False

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()

        with patch("app.api.v1.diagnosis.nav_manager") as mock_nav:
            mock_nav.send_diagnosis_result = AsyncMock()
            await _push_diagnosis_result(mock_result, "req_001", mock_db)
            mock_nav.send_diagnosis_result.assert_called_once_with("sess_001", "req_001")
            assert mock_result.result_pushed is True
            mock_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_failure_does_not_raise(self):
        mock_result = Mock()
        mock_result.session_id = "sess_001"
        mock_result.result_pushed = False

        mock_db = AsyncMock()

        with patch("app.api.v1.diagnosis.nav_manager") as mock_nav:
            mock_nav.send_diagnosis_result = AsyncMock(side_effect=Exception("connection lost"))
            await _push_diagnosis_result(mock_result, "req_001", mock_db)
            assert mock_result.result_pushed is False
