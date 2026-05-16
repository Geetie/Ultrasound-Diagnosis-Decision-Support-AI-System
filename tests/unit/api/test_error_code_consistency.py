import pytest
from app.api.v1.errors import (
    ERROR_CODE_MAP,
    AppBaseException,
    HSMUnavailableException,
    RedisUnavailableNewSessionDelayException,
    SafetyOutputBlockedException,
    SafetyException,
    InfrastructureException,
)


class TestErrorCodeMapConsistency:
    def test_e4008_maps_to_redis_session_delay(self):
        assert ERROR_CODE_MAP.get("E4008").message == "Redis unavailable causing new session creation delay (PostgreSQL fallback)"

    def test_e4013_maps_to_hsm_unavailable(self):
        assert ERROR_CODE_MAP.get("E4013").message == "HSM unavailable"

    def test_e3006_maps_to_safety_output_blocked(self):
        assert ERROR_CODE_MAP.get("E3006").message == "Safety check failed, output blocked"

    def test_e2001_exists(self):
        assert "E2001" in ERROR_CODE_MAP

    def test_e5000_not_in_map(self):
        assert "E5000" not in ERROR_CODE_MAP


class TestHSMUnavailableException:
    def test_error_code_is_e4013(self):
        exc = HSMUnavailableException()
        assert exc.error_code == "E4013"

    def test_default_message(self):
        exc = HSMUnavailableException()
        assert exc.error_message == "HSM unavailable"

    def test_status_code_503(self):
        exc = HSMUnavailableException()
        assert exc.status_code == 503

    def test_retryable(self):
        exc = HSMUnavailableException()
        assert exc.retryable is True


class TestRedisUnavailableNewSessionDelayException:
    def test_error_code_is_e4008(self):
        exc = RedisUnavailableNewSessionDelayException()
        assert exc.error_code == "E4008"

    def test_default_message(self):
        exc = RedisUnavailableNewSessionDelayException()
        assert "Redis unavailable" in exc.error_message

    def test_status_code_503(self):
        exc = RedisUnavailableNewSessionDelayException()
        assert exc.status_code == 503

    def test_has_suggestion(self):
        exc = RedisUnavailableNewSessionDelayException()
        assert exc.suggestion is not None
        assert "PostgreSQL" in exc.suggestion


class TestSafetyOutputBlockedException:
    def test_error_code_is_e3006(self):
        exc = SafetyOutputBlockedException()
        assert exc.error_code == "E3006"

    def test_default_message(self):
        exc = SafetyOutputBlockedException()
        assert "Safety check" in exc.error_message

    def test_is_safety_exception(self):
        exc = SafetyOutputBlockedException()
        assert isinstance(exc, SafetyException)

    def test_status_code_200(self):
        exc = SafetyOutputBlockedException()
        assert exc.status_code == 200

    def test_has_suggestion(self):
        exc = SafetyOutputBlockedException()
        assert exc.suggestion is not None


class TestExceptionHierarchy:
    def test_hsm_is_infrastructure_exception(self):
        exc = HSMUnavailableException()
        assert isinstance(exc, InfrastructureException)
        assert isinstance(exc, AppBaseException)

    def test_redis_unavailable_is_infrastructure_exception(self):
        exc = RedisUnavailableNewSessionDelayException()
        assert isinstance(exc, InfrastructureException)
        assert isinstance(exc, AppBaseException)

    def test_safety_output_blocked_is_safety_exception(self):
        exc = SafetyOutputBlockedException()
        assert isinstance(exc, SafetyException)
        assert isinstance(exc, AppBaseException)
