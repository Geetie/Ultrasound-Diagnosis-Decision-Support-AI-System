import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.redis import RedisGetResult
from app.models.schemas import FeedbackType, FeedbackLevel

pytestmark = [pytest.mark.track_api, pytest.mark.feedback]


def _base_payload(**overrides):
    payload = {
        "request_id": "req_001",
        "session_id": "session_001",
        "feedback_type": "correction",
        "feedback_level": "L2",
        "content": {
            "field": "diagnosis",
            "original_value": "Nodule",
            "corrected_value": "Cyst",
            "reason": "Visual confirmation",
        },
        "reviewer_id": "doctor_001",
    }
    payload.update(overrides)
    return payload


def _mock_feedback_repo():
    repo = AsyncMock()
    mock_feedback = MagicMock()
    mock_feedback.feedback_id = "fb_001"
    repo.create_feedback = AsyncMock(return_value=mock_feedback)
    return repo


def _mock_diagnosis_repo(found=True):
    repo = AsyncMock()
    if found:
        mock_diagnosis = MagicMock()
        mock_diagnosis.request_id = "req_001"
        repo.get_result = AsyncMock(return_value=mock_diagnosis)
    else:
        repo.get_result = AsyncMock(return_value=None)
    return repo


def _mock_session_repo():
    return AsyncMock()


def _mock_audit_service():
    svc = AsyncMock()
    svc.log_action = AsyncMock()
    return svc


class TestFeedbackCorrectionL2:
    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def test_correction_l2_accepted(self, test_client):
        payload = _base_payload(feedback_type="correction", feedback_level="L2")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.RedisPool.setex", new_callable=AsyncMock, return_value=True), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["request_id"] == "req_001"
        assert "feedback_id" in data


class TestFeedbackSupplementL2:
    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def test_supplement_l2_accepted(self, test_client):
        payload = _base_payload(feedback_type="supplement", feedback_level="L2")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.RedisPool.setex", new_callable=AsyncMock, return_value=True), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"


class TestFeedbackNewKnowledgeL2:
    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def test_new_knowledge_l2_accepted(self, test_client):
        payload = _base_payload(feedback_type="new_knowledge", feedback_level="L2")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.RedisPool.setex", new_callable=AsyncMock, return_value=True), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"


class TestFeedbackRuleChangeL3:
    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DEPARTMENT_HEAD"}

        yield

        app.dependency_overrides.clear()

    def test_rule_change_l3_accepted(self, test_client):
        payload = _base_payload(feedback_type="rule_change", feedback_level="L3")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.RedisPool.setex", new_callable=AsyncMock, return_value=True), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"


class TestFeedbackRuleChangeNonL3:
    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def test_rule_change_l2_returns_400(self, test_client):
        payload = _base_payload(feedback_type="rule_change", feedback_level="L2")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "E1001"


class TestFeedbackDiagnosisNotFound:
    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=False)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def test_diagnosis_not_found_returns_404(self, test_client):
        payload = _base_payload(request_id="req_nonexistent")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 404
        data = response.json()
        assert data["error_code"] == "E1002"


class TestFeedbackIdempotencyCacheHit:
    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def test_idempotency_cache_hit_returns_cached(self, test_client):
        cached_data = {
            "status": "accepted",
            "message": "反馈已提交",
            "request_id": "req_001",
            "feedback_id": "fb_cached",
        }
        cached_bytes = json.dumps(cached_data).encode("utf-8")
        result = RedisGetResult(value=cached_bytes, error=False)

        payload = _base_payload(idempotency_key="idem_key_001")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=result), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["feedback_id"] == "fb_cached"


class TestFeedbackIdempotencyTTL:
    def test_idempotency_ttl_is_86400(self):
        from app.api.v1.feedback import FEEDBACK_IDEMPOTENCY_TTL_SECONDS
        assert FEEDBACK_IDEMPOTENCY_TTL_SECONDS == 86400

    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def test_idempotency_setex_uses_correct_ttl(self, test_client):
        payload = _base_payload(idempotency_key="idem_ttl_check")

        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.RedisPool.setex", new_callable=AsyncMock, return_value=True) as mock_setex, \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):

            response = test_client.post("/api/v1/feedback", json=payload)

        assert response.status_code == 200
        mock_setex.assert_called_once()
        call_args = mock_setex.call_args
        assert call_args[0][1] == 86400
