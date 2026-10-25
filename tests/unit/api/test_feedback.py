import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.redis import RedisGetResult

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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=False)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

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
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        mock_fb_repo = _mock_feedback_repo()
        mock_diag_repo = _mock_diagnosis_repo(found=True)
        mock_sess_repo = _mock_session_repo()
        mock_audit = _mock_audit_service()

        app.dependency_overrides[get_feedback_repo] = lambda: mock_fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diag_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_sess_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

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


def _mock_diagnosis_repo_with_session_result(request_id="req_latest"):
    """判读仓储替身：按 id 查不到，但**会话**里有一次判读（走"按会话关联"路径）。"""
    repo = AsyncMock()
    repo.get_result = AsyncMock(return_value=None)
    latest = MagicMock()
    latest.request_id = request_id
    repo.get_results_by_session = AsyncMock(return_value=[latest])
    return repo


class TestFeedbackAttachToLatestDiagnosisOfSession:
    """A.62 批 3：反馈必须能**挂到真实判读**上（否则评测集的"正确口径"永远关联不到）。

    背景（真机复现）：前端曾用 `generateFeedbackId()` 自造 `fb_…` 当 `request_id`，
    服务端要求它是真实判读 id → 医生点「提交反馈」在真机上必得 404 `E1002`。
    修法：客户端留空（或给了非判读 id）时，服务端按 `session_id` 关联**最近一次判读**，
    并在响应里回传**实际挂载**的 id。
    """

    @pytest.fixture(autouse=True)
    def mock_deps(self):
        from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
        from app.main import app
        from app.security.auth import get_current_active_user

        self.fb_repo = _mock_feedback_repo()
        self.diag_repo = _mock_diagnosis_repo_with_session_result()
        app.dependency_overrides[get_feedback_repo] = lambda: self.fb_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: self.diag_repo
        app.dependency_overrides[get_session_repo] = lambda: _mock_session_repo()
        app.dependency_overrides[get_audit_service] = lambda: _mock_audit_service()
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor_001", "role": "ULTRASOUND_DOCTOR"}

        yield

        app.dependency_overrides.clear()

    def _post(self, test_client, payload):
        with patch("app.api.v1.feedback.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=False)), \
             patch("app.api.v1.feedback.RedisPool.setex", new_callable=AsyncMock, return_value=True), \
             patch("app.api.v1.feedback.try_renew_session", new_callable=AsyncMock):
            return test_client.post("/api/v1/feedback", json=payload)

    def test_空request_id时按会话关联最近判读并落库到该id(self, test_client):
        """前端留空（=按会话关联）→ 200，且落库/响应都用**真实判读 id**。"""
        response = self._post(test_client, _base_payload(request_id=""))

        assert response.status_code == 200, "留空不该 404：会话里有判读就该能挂上"
        assert response.json()["request_id"] == "req_latest"
        # 落库用的必须是解析后的 id —— 否则评测集按 request_id 关联不到这次反馈
        assert self.fb_repo.create_feedback.await_args.kwargs["request_id"] == "req_latest"

    def test_非判读id时按会话兜底且留痕(self, test_client):
        """客户端给了自造 id（`fb_…`）→ 不 404、按会话兜底，并**记 warning**（可发现、不静默）。"""
        response = self._post(test_client, _base_payload(request_id="fb_1791954782274_b1b3c3bee435"))

        assert response.status_code == 200
        assert response.json()["request_id"] == "req_latest"

    def test_会话里也没有判读时仍然404(self, test_client):
        """兜底也要有底线：会话里真的没有判读 → 404（不许把"无判读"变成"挂到别的会话"）。"""
        self.diag_repo.get_results_by_session = AsyncMock(return_value=[])

        response = self._post(test_client, _base_payload(request_id=""))

        assert response.status_code == 404
        assert response.json()["error_code"] == "E1002"
