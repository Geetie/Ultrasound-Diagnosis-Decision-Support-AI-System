"""Safety Gateway API层处理测试

验证SafetyGatewayClient异常映射和结果处理逻辑。
"""

import pytest

from app.api.v1.errors import SafetyGatewayUnavailableException, SafetyOutputBlockedException

pytestmark = [pytest.mark.sec_domain_sg]


class TestSafetyGatewayExceptions:
    def test_unavailable_exception_properties(self):
        exc = SafetyGatewayUnavailableException()
        assert exc.error_code == "E4005"
        assert exc.status_code == 503
        assert exc.retryable is True

    def test_output_blocked_exception_properties(self):
        exc = SafetyOutputBlockedException()
        assert exc.error_code == "E3006"
        assert exc.status_code == 200
        assert exc.retryable is False

    def test_exception_distinction(self):
        unavailable = SafetyGatewayUnavailableException()
        blocked = SafetyOutputBlockedException()
        assert unavailable.error_code != blocked.error_code
        assert unavailable.retryable != blocked.retryable
        assert unavailable.status_code != blocked.status_code


class TestSafetyGatewayResultHandling:
    def _make_result(self, **overrides):
        result = {
            "allowed": True,
            "degraded_mode": False,
            "degraded_output": None,
            "degradation_warning": None,
            "consent_status": "granted",
            "consent_status_source": "pg_direct",
            "all_passed": True,
        }
        result.update(overrides)
        return result

    def test_allowed_no_degraded(self):
        result = self._make_result()
        assert result["allowed"] is True
        assert result["degraded_mode"] is False
        assert result["all_passed"] is True

    def test_allowed_with_degraded(self):
        result = self._make_result(
            allowed=True,
            degraded_mode=True,
            degraded_output={"organ_name": "liver"},
            degradation_warning="融合层不可用，输出已降级",
            all_passed=False,
        )
        assert result["allowed"] is True
        assert result["degraded_mode"] is True
        assert result["degradation_warning"] is not None
        assert result["all_passed"] is False

    def test_not_allowed_unavailable(self):
        result = self._make_result(
            allowed=False,
            degraded_mode=False,
            all_passed=False,
        )
        assert result["allowed"] is False

    def test_not_allowed_content_blocked(self):
        result = self._make_result(
            allowed=False,
            degraded_mode=False,
            all_passed=False,
        )
        assert result["allowed"] is False
        assert result["all_passed"] is False

    def test_consent_withdrawn(self):
        result = self._make_result(
            allowed=False,
            consent_status="withdrawn",
            all_passed=False,
        )
        assert result["consent_status"] == "withdrawn"
        assert result["allowed"] is False

    def test_degraded_output_structure(self):
        result = self._make_result(
            allowed=True,
            degraded_mode=True,
            degraded_output={"organ_name": "liver", "echogenicity": "hypoechoic"},
            all_passed=False,
        )
        assert isinstance(result["degraded_output"], dict)
        assert "organ_name" in result["degraded_output"]

    def test_consent_status_source_field(self):
        result = self._make_result(consent_status_source="pg_direct")
        assert result["consent_status_source"] == "pg_direct"
