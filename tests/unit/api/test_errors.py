import pytest

from app.api.v1.errors import (
    ERROR_CODE_MAP,
    AdversarialInputException,
    AppBaseException,
    BadRequestException,
    ConfidenceBelowThresholdException,
    DesensitizationCheckTimeoutException,
    DeviceIsolatedException,
    DiagnosisQueueFullException,
    DICOMFormatException,
    DualServiceUnavailableException,
    EdgeNavModelLoadFailedException,
    EdgeSchedulerUnreachableException,
    FusionLayerUnavailableException,
    GPUOverheatException,
    HSMUnavailableException,
    InfrastructureException,
    InvalidIdempotencyKeyException,
    KnowledgeBaseSafetyUpdateException,
    KnowledgeRetrievalTimeoutException,
    KnowledgeVersionIncompatibleException,
    MissingDICOMTagException,
    ModelHotReloadException,
    ModelInferenceFailedException,
    NotFoundException,
    PatientConsentWithdrawnException,
    PatientNotConsentedException,
    PostgreSQLUnavailableException,
    PostgreSQLWriteTimeoutException,
    RateLimitExceededException,
    RedisUnavailableException,
    RedisUnavailableNewSessionDelayException,
    RetrievalNoResultException,
    RetrievalServiceDegradedException,
    RetryLimitExceededException,
    SafetyException,
    SafetyGatewayDownException,
    SafetyGatewayUnavailableException,
    SafetyOutputBlockedException,
    SelfRAGQualityCheckFailedException,
    ServerErrorException,
    SpecialityConclusionContradictException,
    SpecialityConclusionPartialException,
    SystemQueueFullException,
    TripleValidationFailedException,
    UnsupportedExamTypeException,
    UserQueueQuotaExceededException,
    get_error_message,
)

pytestmark = pytest.mark.track_api


class TestErrorCodeRegistry:
    def test_error_code_map_populated(self):
        assert len(ERROR_CODE_MAP) > 0

    def test_e1001_registered(self):
        assert "E1001" in ERROR_CODE_MAP
        assert ERROR_CODE_MAP["E1001"].message == "请求参数不合法"

    def test_e1002_registered(self):
        assert "E1002" in ERROR_CODE_MAP

    def test_e1007_registered(self):
        assert "E1007" in ERROR_CODE_MAP

    def test_e1008_registered(self):
        assert "E1008" in ERROR_CODE_MAP

    def test_e2001_registered(self):
        assert "E2001" in ERROR_CODE_MAP

    def test_e3001_registered(self):
        assert "E3001" in ERROR_CODE_MAP

    def test_e4005_registered(self):
        assert "E4005" in ERROR_CODE_MAP

    def test_e4013_registered(self):
        assert "E4013" in ERROR_CODE_MAP


class TestGetErrorMessage:
    def test_known_error_code(self):
        msg = get_error_message("E1001")
        assert msg == "请求参数不合法"

    def test_unknown_error_code(self):
        msg = get_error_message("E9999")
        assert msg == "Unknown error"


class TestClientErrorExceptionHierarchy:
    def test_bad_request(self):
        exc = BadRequestException()
        assert exc.error_code == "E1001"
        assert exc.status_code == 400

    def test_not_found(self):
        exc = NotFoundException()
        assert exc.error_code == "E1002"
        assert exc.status_code == 404

    def test_rate_limit_exceeded(self):
        exc = RateLimitExceededException()
        assert exc.error_code == "E1007"
        assert exc.status_code == 429
        assert exc.retryable is True
        assert exc.retry_strategy == "retry_after_5s"

    def test_missing_dicom_tag(self):
        exc = MissingDICOMTagException()
        assert exc.error_code == "E1012"
        assert exc.status_code == 400

    def test_adversarial_input(self):
        exc = AdversarialInputException()
        assert exc.error_code == "E1006"
        assert exc.status_code == 400

    def test_retry_limit_exceeded(self):
        exc = RetryLimitExceededException()
        assert exc.error_code == "E1009"
        assert exc.status_code == 429
        assert exc.retryable is True

    def test_unsupported_exam_type(self):
        exc = UnsupportedExamTypeException()
        assert exc.error_code == "E1011"
        assert exc.status_code == 400

    def test_invalid_idempotency_key(self):
        exc = InvalidIdempotencyKeyException()
        assert exc.error_code == "E1013"
        assert exc.status_code == 400

    def test_dicom_format_error(self):
        exc = DICOMFormatException()
        assert exc.error_code == "E1004"
        assert exc.status_code == 400


class TestConsentExceptions:
    def test_not_consented(self):
        exc = PatientNotConsentedException()
        assert exc.error_code == "E1008"
        assert exc.status_code == 403
        assert exc.suggestion is not None

    def test_consent_withdrawn(self):
        exc = PatientConsentWithdrawnException()
        assert exc.error_code == "E1010"
        assert exc.status_code == 403
        assert exc.suggestion is not None


class TestServerExceptionHierarchy:
    def test_server_error(self):
        exc = ServerErrorException()
        assert exc.error_code == "E2000"
        assert exc.status_code == 500

    def test_knowledge_retrieval_timeout(self):
        exc = KnowledgeRetrievalTimeoutException()
        assert exc.error_code == "E2001"
        assert exc.retryable is True

    def test_model_inference_failed(self):
        exc = ModelInferenceFailedException()
        assert exc.error_code == "E2002"
        assert exc.retryable is True

    def test_diagnosis_queue_full(self):
        exc = DiagnosisQueueFullException()
        assert exc.error_code == "E2004"
        assert exc.status_code == 503

    def test_fusion_layer_unavailable(self):
        exc = FusionLayerUnavailableException()
        assert exc.error_code == "E2003"
        assert exc.status_code == 503

    def test_system_queue_full(self):
        exc = SystemQueueFullException()
        assert exc.error_code == "E2004A"
        assert exc.status_code == 503

    def test_user_queue_quota_exceeded(self):
        exc = UserQueueQuotaExceededException()
        assert exc.error_code == "E2004B"
        assert exc.status_code == 503

    def test_retrieval_no_result(self):
        exc = RetrievalNoResultException()
        assert exc.error_code == "E2006"
        assert exc.suggestion is not None

    def test_model_hot_reload(self):
        exc = ModelHotReloadException()
        assert exc.error_code == "E2007"
        assert exc.status_code == 503


class TestSafetyExceptionHierarchy:
    def test_safety_exception_status_200(self):
        exc = SafetyException(error_code="E3xxx", error_message="test")
        assert exc.status_code == 200

    def test_confidence_below_threshold(self):
        exc = ConfidenceBelowThresholdException()
        assert exc.error_code == "E3001"

    def test_triple_validation_failed(self):
        exc = TripleValidationFailedException()
        assert exc.error_code == "E3002"

    def test_self_rag_quality_check_failed(self):
        exc = SelfRAGQualityCheckFailedException()
        assert exc.error_code == "E3003"
        assert exc.retryable is True

    def test_speciality_conclusion_contradict(self):
        exc = SpecialityConclusionContradictException()
        assert exc.error_code == "E3004"

    def test_speciality_conclusion_partial(self):
        exc = SpecialityConclusionPartialException()
        assert exc.error_code == "E3005"

    def test_safety_output_blocked(self):
        exc = SafetyOutputBlockedException()
        assert exc.error_code == "E3006"


class TestInfrastructureExceptionHierarchy:
    def test_safety_gateway_unavailable(self):
        exc = SafetyGatewayUnavailableException()
        assert exc.error_code == "E4005"
        assert exc.status_code == 503
        assert exc.retryable is True

    def test_device_isolated(self):
        exc = DeviceIsolatedException()
        assert exc.error_code == "E4010"
        assert exc.status_code == 403

    def test_knowledge_version_incompatible(self):
        exc = KnowledgeVersionIncompatibleException()
        assert exc.error_code == "E4004"

    def test_knowledge_base_safety_update(self):
        exc = KnowledgeBaseSafetyUpdateException()
        assert exc.error_code == "E4006"
        assert exc.suggestion is not None

    def test_safety_gateway_down(self):
        exc = SafetyGatewayDownException()
        assert exc.error_code == "E4005A"

    def test_postgresql_unavailable(self):
        exc = PostgreSQLUnavailableException()
        assert exc.error_code == "E4005B"

    def test_dual_service_unavailable(self):
        exc = DualServiceUnavailableException()
        assert exc.error_code == "E4005C"

    def test_hsm_unavailable(self):
        exc = HSMUnavailableException()
        assert exc.error_code == "E4013"

    def test_redis_delay(self):
        exc = RedisUnavailableNewSessionDelayException()
        assert exc.error_code == "E4008"
        assert exc.suggestion is not None

    def test_edge_scheduler_unreachable(self):
        exc = EdgeSchedulerUnreachableException()
        assert exc.error_code == "E4001"

    def test_edge_nav_model_load_failed(self):
        exc = EdgeNavModelLoadFailedException()
        assert exc.error_code == "E4002"

    def test_desensitization_check_timeout(self):
        exc = DesensitizationCheckTimeoutException()
        assert exc.error_code == "E4003"

    def test_redis_unavailable(self):
        exc = RedisUnavailableException()
        assert exc.error_code == "E4007"

    def test_postgresql_write_timeout(self):
        exc = PostgreSQLWriteTimeoutException()
        assert exc.error_code == "E4009"

    def test_gpu_overheat(self):
        exc = GPUOverheatException()
        assert exc.error_code == "E4011"

    def test_retrieval_service_degraded(self):
        exc = RetrievalServiceDegradedException()
        assert exc.error_code == "E4012"


class TestExceptionInheritance:
    def test_client_errors_inherit_app_base(self):
        assert issubclass(BadRequestException, AppBaseException)
        assert issubclass(NotFoundException, AppBaseException)
        assert issubclass(RateLimitExceededException, AppBaseException)

    def test_safety_errors_inherit_safety_exception(self):
        assert issubclass(ConfidenceBelowThresholdException, SafetyException)
        assert issubclass(SafetyOutputBlockedException, SafetyException)

    def test_infrastructure_errors_inherit_infrastructure(self):
        assert issubclass(SafetyGatewayUnavailableException, InfrastructureException)
        assert issubclass(RedisUnavailableException, InfrastructureException)

    def test_safety_inherits_app_base(self):
        assert issubclass(SafetyException, AppBaseException)

    def test_infrastructure_inherits_app_base(self):
        assert issubclass(InfrastructureException, AppBaseException)
