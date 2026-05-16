import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from safety_gateway.validation_result import CheckResult, CheckResultStatus, ConsentStatus, DegradedReason

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestSafetyCheckHTTPRoute:

    @pytest.fixture
    def mock_checker(self):
        checker = MagicMock()
        checker.perform_full_check = AsyncMock()
        checker.apply_degraded_output = MagicMock(return_value={"organ_name": "thyroid", "degraded_reason": "sensitive_data_detected", "conclusion_suppressed": True})
        checker.close = AsyncMock()
        return checker

    @pytest.fixture
    def mock_health_checker(self):
        hc = MagicMock()
        hc.check_all = AsyncMock(return_value={"status": "healthy", "components": {}})
        hc._latency_tracker = MagicMock()
        hc._latency_tracker.is_throttling = False
        hc.aclose = AsyncMock()
        return hc

    def test_safety_check_endpoint_returns_passed(self, mock_checker, mock_health_checker):
        from fastapi.testclient import TestClient
        from safety_gateway.main import app

        mock_checker.perform_full_check.return_value = CheckResult(
            status=CheckResultStatus.PASSED,
            consent_status=ConsentStatus.GRANTED,
            message="校验通过",
            elapsed_ms=1.5,
            consent_status_source="pg_direct",
        )

        with patch("safety_gateway.main.get_safety_gateway_checker", return_value=mock_checker), \
             patch("safety_gateway.main._sg_concurrency_semaphore") as mock_sem, \
             patch("safety_gateway.checker._get_health_checker", return_value=mock_health_checker), \
             patch("safety_gateway.health.memory_monitor.MemoryMonitor.is_over_budget", return_value=False):
            mock_sem.acquire = AsyncMock()
            mock_sem.release = MagicMock()
            client = TestClient(app)
            response = client.post(
                "/api/v1/safety/check",
                json={
                    "patient_id": "P001",
                    "output_data": {"organ_name": "thyroid"},
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["passed"] is True
        assert data["all_passed"] is True

    def test_safety_check_endpoint_returns_degraded(self, mock_checker, mock_health_checker):
        from fastapi.testclient import TestClient
        from safety_gateway.main import app

        mock_checker.perform_full_check.return_value = CheckResult(
            status=CheckResultStatus.PASSED,
            consent_status=ConsentStatus.GRANTED,
            message="敏感数据检测",
            elapsed_ms=2.0,
            degraded_mode=True,
            degraded_reason=DegradedReason.SENSITIVE_DATA_DETECTED,
            sensitive_data_detected=["sg_phone"],
            consent_status_source="pg_direct",
        )

        with patch("safety_gateway.main.get_safety_gateway_checker", return_value=mock_checker), \
             patch("safety_gateway.main._sg_concurrency_semaphore") as mock_sem, \
             patch("safety_gateway.checker._get_health_checker", return_value=mock_health_checker), \
             patch("safety_gateway.health.memory_monitor.MemoryMonitor.is_over_budget", return_value=False):
            mock_sem.acquire = AsyncMock()
            mock_sem.release = MagicMock()
            client = TestClient(app)
            response = client.post(
                "/api/v1/safety/check",
                json={
                    "patient_id": "P001",
                    "output_data": {"organ_name": "thyroid", "phone": "13912345678"},
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["passed"] is True
        assert data["degraded_mode"] is True
        assert data["all_passed"] is False

    def test_health_endpoint_structure(self, mock_health_checker):
        from fastapi.testclient import TestClient
        from safety_gateway.main import app

        with patch("safety_gateway.checker._get_health_checker", return_value=mock_health_checker):
            client = TestClient(app)
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "components" in data

    def test_root_endpoint(self):
        from fastapi.testclient import TestClient
        from safety_gateway.main import app

        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "safety-gateway"
