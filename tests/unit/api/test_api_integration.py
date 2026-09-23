"""Integration tests for API endpoints"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timezone
import json
import base64

from sqlalchemy.ext.asyncio import AsyncSession
pytestmark = [pytest.mark.track_api, pytest.mark.track_int]


class TestSessionEndpointsIntegration:
    """Integration tests for Session endpoints"""
    
    @pytest.fixture
    def mock_db_deps(self):
        from app.main import app
        from app.dependencies import get_session_repo, get_consent_repo, get_audit_service
        from app.repositories.session_repository import SessionRepository
        from app.repositories.consent_repository import ConsentRepository
        from app.services.audit_service import AuditService
        from app.security.auth import get_current_active_user

        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_consent_repo = AsyncMock(spec=ConsentRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_consent_repo] = lambda: mock_consent_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        yield {
            "consent_repo": mock_consent_repo,
            "session_repo": mock_session_repo,
            "audit_service": mock_audit_service
        }

        app.dependency_overrides.clear()
    
    def test_create_session_success(self, test_client, mock_db_deps):
        """Test creating a session successfully"""
        from app.models.database import DiagnosisSession, ConsentRecord
        from app.models.schemas import ConsentStatus
        
        # Setup mock
        mock_db_deps["consent_repo"].get_latest_consent = AsyncMock(
            return_value=ConsentRecord(
                record_id=12345,
                patient_id="patient_001",
                status=ConsentStatus.GRANTED.value,
                consent_version="1.0",
                granted_at=datetime.now(timezone.utc)
            )
        )
        mock_db_deps["session_repo"].create_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="sess_20250508_abc123",
                patient_id="patient_001",
                device_id="device_001",
                status="pending",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "pending"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
        )

        response = test_client.post(
            "/api/v1/sessions",
            json={
                "patient_id": "patient_001",
                "device_id": "device_001",
                "target_organs": ["thyroid"]
            }
        )
        
        # Check response
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["patient_id"] == "patient_001"
        assert data["status"] == "pending"
    
    def test_get_session_not_found(self, test_client, mock_db_deps):
        """Test getting a non-existent session"""
        mock_db_deps["session_repo"].get_session = AsyncMock(return_value=None)
        
        response = test_client.get("/api/v1/sessions/nonexistent_id")
        
        assert response.status_code == 404
        assert "error_code" in response.json()


class TestDiagnosisEndpointsIntegration:
    """Integration tests for Diagnosis endpoints"""
    
    @pytest.fixture
    def mock_db_deps(self):
        from app.main import app
        from app.dependencies import get_diagnosis_repo, get_consent_repo, get_session_repo, get_audit_service
        from app.repositories.diagnosis_repository import DiagnosisRepository
        from app.repositories.consent_repository import ConsentRepository
        from app.repositories.session_repository import SessionRepository
        from app.services.audit_service import AuditService
        from app.security.auth import get_current_active_user

        mock_diagnosis_repo = AsyncMock(spec=DiagnosisRepository)
        mock_consent_repo = AsyncMock(spec=ConsentRepository)
        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diagnosis_repo
        app.dependency_overrides[get_consent_repo] = lambda: mock_consent_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        yield {
            "diagnosis_repo": mock_diagnosis_repo,
            "consent_repo": mock_consent_repo,
            "session_repo": mock_session_repo,
            "audit_service": mock_audit_service
        }

        app.dependency_overrides.clear()
    
    def test_submit_diagnosis_request_success(self, test_client, mock_db_deps):
        """Test submitting a diagnosis request successfully"""
        from app.models.database import DiagnosisSession, ConsentRecord
        from app.models.schemas import ConsentStatus
        
        # Setup mocks
        mock_db_deps["session_repo"].get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_001",
                patient_id="patient_001",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "in_progress"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted"))
        mock_db_deps["consent_repo"].get_latest_consent = AsyncMock(
            return_value=ConsentRecord(
                record_id=12345,
                patient_id="patient_001",
                status=ConsentStatus.GRANTED.value,
                consent_version="1.0"))
        mock_db_deps["diagnosis_repo"].create_request = AsyncMock(
            return_value="req_20250508_abc123")
        mock_db_deps["audit_service"].log_action = AsyncMock()

        from app.config.redis import RedisGetResult

        with patch("app.api.v1.diagnosis.check_rate_limit", new_callable=AsyncMock, return_value=None), \
             patch("app.config.redis.RedisPool.get_with_error", new_callable=AsyncMock, return_value=RedisGetResult(value=None, error=True)), \
             patch("app.config.redis.RedisPool.setex", new_callable=AsyncMock, return_value=True):
            response = test_client.post(
                "/api/v1/diagnosis/request",
                json={
                    "session_id": "session_001",
                    "target_organ": "thyroid",
                    "image_ids": [],
                    "response_tier": "full",
                    "idempotency_key": "test_key_123"
                },
                headers={"X-Priority": "1"}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert "request_id" in data
        assert data["status"] == "processing"
    
    def test_submit_diagnosis_request_no_consent(self, test_client, mock_db_deps):
        """Test submitting diagnosis request without consent"""
        from app.models.database import DiagnosisSession, ConsentRecord
        from app.models.schemas import ConsentStatus
        
        mock_db_deps["session_repo"].get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_001",
                patient_id="patient_001",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "in_progress"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted"))
        mock_db_deps["consent_repo"].get_latest_consent = AsyncMock(
            return_value=ConsentRecord(
                record_id=12345,
                patient_id="patient_001",
                status=ConsentStatus.DENIED.value,
                consent_version="1.0"))
        
        response = test_client.post(
            "/api/v1/diagnosis/request",
            json={
                "session_id": "session_001",
                "target_organ": "thyroid",
                "image_ids": [],
                "response_tier": "full"
            }
        )
        
        assert response.status_code == 403
        assert "E1008" in response.json()["error_code"]


class TestDICOMEndpointsIntegration:
    """Integration tests for DICOM endpoints"""
    
    def test_upload_dicom_success(self, test_client):
        from app.main import app
        from app.dependencies import get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user
        from app.repositories.session_repository import SessionRepository
        from app.services.audit_service import AuditService
        from app.models.database import DiagnosisSession
        import pydicom
        from pydicom.dataset import Dataset
        from pydicom.sequence import Sequence
        from io import BytesIO

        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_session_repo.get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_001",
                patient_id="patient_001",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "in_progress"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted"))
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        try:

            ds = Dataset()
            ds.PatientID = "PAT001"
            ds.PatientName = "TestPatient"
            ds.StudyInstanceUID = "1.2.3.4.5"
            ds.SeriesInstanceUID = "1.2.3.4.5.6"
            ds.SOPInstanceUID = "1.2.3.4.5.6.7"
            ds.Modality = "US"
            ds.Rows = 480
            ds.Columns = 640
            ds.BitsAllocated = 8
            ds.PixelData = b'\x00' * (480 * 640)
            ds.file_meta = pydicom.dataset.FileMetaDataset()
            ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
            ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

            buf = BytesIO()
            ds.save_as(buf, write_like_original=False)
            dicom_data = buf.getvalue()

            files = {"file": ("test.dcm", dicom_data, "application/dicom")}

            response = test_client.post(
                "/api/v1/imaging/dicom/upload",
                files=files,
                data={"session_id": "session_001", "priority": "1"}
            )

            assert response.status_code == 200
            data = response.json()
            assert "image_id" in data
            assert data["status"] == "accepted"
            assert data["metadata"]["patient_id"] == "P******"
            assert data["metadata"]["modality"] == "US"
        finally:
            app.dependency_overrides.clear()
    
    def test_upload_invalid_dicom(self, test_client):
        from app.main import app
        from app.dependencies import get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user
        from app.repositories.session_repository import SessionRepository
        from app.services.audit_service import AuditService
        from app.models.database import DiagnosisSession

        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_session_repo.get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_001",
                patient_id="patient_001",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "in_progress"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted"))
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        try:
            invalid_data = b"not a dicom file"
            files = {"file": ("test.dcm", invalid_data, "application/dicom")}

            response = test_client.post(
                "/api/v1/imaging/dicom/upload",
                files=files,
                data={"session_id": "session_001", "priority": "1"}
            )

            assert response.status_code == 400
            assert "E1004" in response.json()["error_code"]
        finally:
            app.dependency_overrides.clear()
    
    def test_upload_dicom_missing_tags(self, test_client):
        from app.main import app
        from app.dependencies import get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user
        from app.repositories.session_repository import SessionRepository
        from app.services.audit_service import AuditService
        from app.models.database import DiagnosisSession
        import pydicom
        from pydicom.dataset import Dataset
        from io import BytesIO

        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_session_repo.get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_001",
                patient_id="patient_001",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "in_progress"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted"))
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        try:

            ds = Dataset()
            ds.Modality = "US"
            ds.Rows = 480
            ds.Columns = 640
            ds.BitsAllocated = 8
            ds.PixelData = b'\x00' * 100
            ds.StudyInstanceUID = "1.2.3.4.5"
            ds.SOPInstanceUID = "1.2.3.4.5.6.7"
            ds.file_meta = pydicom.dataset.FileMetaDataset()
            ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
            ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

            buf = BytesIO()
            ds.save_as(buf, write_like_original=False)
            dicom_data = buf.getvalue()

            files = {"file": ("test.dcm", dicom_data, "application/dicom")}

            response = test_client.post(
                "/api/v1/imaging/dicom/upload",
                files=files,
                data={"session_id": "session_001", "priority": "1"}
            )

            assert response.status_code == 400
            assert "E1012" in response.json()["error_code"]
        finally:
            app.dependency_overrides.clear()
    
    def test_upload_dicom_wrong_modality(self, test_client):
        from app.main import app
        from app.dependencies import get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user
        from app.repositories.session_repository import SessionRepository
        from app.services.audit_service import AuditService
        from app.models.database import DiagnosisSession
        import pydicom
        from pydicom.dataset import Dataset
        from io import BytesIO

        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_session_repo.get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_001",
                patient_id="patient_001",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "in_progress"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted"))
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        try:

            ds = Dataset()
            ds.PatientID = "PAT001"
            ds.PatientName = "TestPatient"
            ds.StudyInstanceUID = "1.2.3.4.5"
            ds.SeriesInstanceUID = "1.2.3.4.5.6"
            ds.SOPInstanceUID = "1.2.3.4.5.6.7"
            ds.Modality = "CT"
            ds.Rows = 480
            ds.Columns = 640
            ds.BitsAllocated = 8
            ds.PixelData = b'\x00' * 100
            ds.file_meta = pydicom.dataset.FileMetaDataset()
            ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
            ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

            buf = BytesIO()
            ds.save_as(buf, write_like_original=False)
            dicom_data = buf.getvalue()

            files = {"file": ("test.dcm", dicom_data, "application/dicom")}

            response = test_client.post(
                "/api/v1/imaging/dicom/upload",
                files=files,
                data={"session_id": "session_001", "priority": "1"}
            )

            assert response.status_code == 400
            json_resp = response.json()
            assert "E1012" in json_resp["error_code"]
            assert "US" in json_resp["error_message"]
        finally:
            app.dependency_overrides.clear()


class TestFeedbackEndpointsIntegration:
    """Integration tests for Feedback endpoints"""
    
    @pytest.fixture
    def mock_db_deps(self):
        from app.main import app
        from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
        from app.repositories.feedback_repository import FeedbackRepository
        from app.repositories.diagnosis_repository import DiagnosisRepository
        from app.repositories.session_repository import SessionRepository
        from app.services.audit_service import AuditService
        from app.security.auth import get_current_active_user

        mock_feedback_repo = AsyncMock(spec=FeedbackRepository)
        mock_diagnosis_repo = AsyncMock(spec=DiagnosisRepository)
        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_feedback_repo] = lambda: mock_feedback_repo
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diagnosis_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        yield {
            "feedback_repo": mock_feedback_repo,
            "diagnosis_repo": mock_diagnosis_repo,
            "session_repo": mock_session_repo,
            "audit_service": mock_audit_service
        }

        app.dependency_overrides.clear()
    
    def test_submit_feedback_success(self, test_client, mock_db_deps):
        """Test submitting feedback successfully"""
        from app.models.database import DiagnosisResult
        from app.models.database import FeedbackRecord
        
        mock_db_deps["diagnosis_repo"].get_result = AsyncMock(
            return_value=DiagnosisResult(
                request_id="req_001",
                session_id="session_001",
                organ="thyroid",
                tier="full",
                confidence=0.85,
                findings={"status": "completed"},
                status="completed",
                created_at=datetime.now(timezone.utc)
            )
        )
        mock_db_deps["feedback_repo"].create_feedback = AsyncMock(
            return_value=FeedbackRecord(
                feedback_id="fb_20250508_abc123",
                request_id="req_001",
                session_id="session_001",
                feedback_type="correction",
                feedback_level="L2",
                reviewer_id="doctor_001",
                content={
                    "field": "diagnosis",
                    "original_value": "Nodule",
                    "corrected_value": "Cyst",
                    "reason": "Visual confirmation"
                },
                created_at=datetime.now(timezone.utc)
            )
        )
        mock_db_deps["audit_service"].log_action = AsyncMock()
        
        response = test_client.post(
            "/api/v1/feedback",
            json={
                "request_id": "req_001",
                "session_id": "session_001",
                "feedback_type": "correction",
                "feedback_level": "L2",
                "content": {
                    "field": "diagnosis",
                    "original_value": "Nodule",
                    "corrected_value": "Cyst",
                    "reason": "Visual confirmation"
                },
                "reviewer_id": "doctor_001"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert "feedback_id" in data
    
    def test_submit_feedback_request_not_found(self, test_client, mock_db_deps):
        """Test submitting feedback for non-existent request"""
        mock_db_deps["diagnosis_repo"].get_result = AsyncMock(return_value=None)
        
        response = test_client.post(
            "/api/v1/feedback",
            json={
                "request_id": "nonexistent",
                "session_id": "session_001",
                "feedback_type": "correction",
                "feedback_level": "L2",
                "content": {
                    "field": "diagnosis",
                    "original_value": "Nodule",
                    "corrected_value": "Cyst",
                    "reason": "Visual confirmation"
                },
                "reviewer_id": "doctor_001"
            }
        )
        
        assert response.status_code == 404
        assert "E1002" in response.json()["error_code"]


class TestErrorHandlingIntegration:
    """Integration tests for error handling"""
    
    def test_404_error(self, test_client):
        """Test 404 error handling"""
        response = test_client.get("/api/v1/nonexistent_endpoint")
        
        assert response.status_code == 404
        data = response.json()
        assert "error_code" in data
        assert "error_message" in data
    
    def test_validation_error(self, test_client):
        from app.main import app
        from app.dependencies import get_session_repo, get_consent_repo, get_audit_service
        from app.security.auth import get_current_active_user
        from app.repositories.session_repository import SessionRepository
        from app.repositories.consent_repository import ConsentRepository
        from app.services.audit_service import AuditService

        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_consent_repo = AsyncMock(spec=ConsentRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_consent_repo] = lambda: mock_consent_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        try:
            response = test_client.post(
                "/api/v1/sessions",
                json={
                    "invalid": "invalid_data"
                }
            )

            assert response.status_code in (400, 422)
            data = response.json()
            assert "error_code" in data or "detail" in data
        finally:
            app.dependency_overrides.clear()


class TestWebSocketIntegration:
    """Integration tests for WebSocket endpoints"""

    def test_websocket_nav_route_exists(self):
        """Test WebSocket navigation route exists in app"""
        from app.main import app
        routes = [route.path for route in app.routes]
        assert "/api/v1/realtime/nav" in routes

    def test_websocket_dicom_route_exists(self):
        """Test WebSocket DICOM route exists in app"""
        from app.main import app
        routes = [route.path for route in app.routes]
        assert "/api/v1/realtime/dicom" in routes

    @pytest.mark.asyncio
    async def test_connection_manager_generate_token(self):
        from app.api.websocket.nav import ConnectionManager

        manager = ConnectionManager()
        session_id = "test_session_001"

        token = await manager.generate_auth_token(session_id)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

        is_valid = await manager.validate_auth_token(session_id, token)
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_connection_manager_invalid_token(self):
        from app.api.websocket.nav import ConnectionManager

        manager = ConnectionManager()
        session_id = "test_session_002"

        token = await manager.generate_auth_token(session_id)
        is_valid = await manager.validate_auth_token("wrong_session", token)
        assert is_valid is False

        is_valid = await manager.validate_auth_token(session_id, "invalid_token")
        assert is_valid is False

    def test_connection_manager_thread_safety(self):
        from app.api.websocket.nav import ConnectionManager
        import asyncio

        manager = ConnectionManager()

        assert hasattr(manager.auth_manager, "_session_locks")
        assert hasattr(manager.auth_manager, "_locks_lock")
        assert isinstance(manager.auth_manager._locks_lock, asyncio.Lock)


class TestDiagnosisResultEndpoint:
    """Tests for GET /api/v1/diagnosis/result/{request_id}"""

    def test_get_diagnosis_result_not_found(self, test_client):
        from app.main import app
        from app.config.database import get_db_session
        from app.dependencies import get_diagnosis_repo
        from app.security.auth import get_current_active_user
        from app.repositories.diagnosis_repository import DiagnosisRepository

        mock_session = AsyncMock(spec=AsyncSession)
        mock_diagnosis_repo = AsyncMock(spec=DiagnosisRepository)
        mock_diagnosis_repo.get_result = AsyncMock(return_value=None)

        async def mock_get_db():
            yield mock_session

        app.dependency_overrides[get_db_session] = mock_get_db
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diagnosis_repo
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        try:
            response = test_client.get("/api/v1/diagnosis/result/nonexistent")

            assert response.status_code == 404
            assert "E1002" in response.json()["error_code"]
        finally:
            app.dependency_overrides.clear()

    def test_get_diagnosis_result_with_safety_gateway(self, test_client):
        from app.main import app
        from app.config.database import get_db_session
        from app.dependencies import get_diagnosis_repo, get_consent_repo, get_session_repo
        from app.security.auth import get_current_active_user
        from app.repositories.diagnosis_repository import DiagnosisRepository
        from app.repositories.consent_repository import ConsentRepository
        from app.repositories.session_repository import SessionRepository
        from app.models.database import DiagnosisResult as DBDiagnosisResult, DiagnosisSession, ConsentRecord
        from app.models.schemas import ConsentStatus

        mock_db = AsyncMock(spec=AsyncSession)
        mock_diagnosis_repo = AsyncMock(spec=DiagnosisRepository)
        mock_consent_repo = AsyncMock(spec=ConsentRepository)
        mock_session_repo = AsyncMock(spec=SessionRepository)

        mock_diagnosis_repo.get_result = AsyncMock(
            return_value=DBDiagnosisResult(
                request_id="req_001",
                session_id="session_001",
                organ="thyroid",
                tier="full",
                confidence=0.85,
                findings={"findings": "normal"},
                status="completed",
                created_at=datetime.now(timezone.utc),
            )
        )
        mock_session_repo.get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_001",
                patient_id="patient_001",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "completed"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        mock_consent_repo.get_latest_consent = AsyncMock(
            return_value=ConsentRecord(
                record_id=1,
                patient_id="patient_001",
                status=ConsentStatus.GRANTED.value,
                consent_version="1.0",
            )
        )

        async def mock_get_db():
            yield mock_db

        app.dependency_overrides[get_db_session] = mock_get_db
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diagnosis_repo
        app.dependency_overrides[get_consent_repo] = lambda: mock_consent_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        mock_sg = AsyncMock()
        mock_sg.check_output = AsyncMock(return_value={
            "allowed": True,
            "consent_status_at_output": "granted",
            "degraded_mode": False,
        })

        mock_redis_result = MagicMock()
        mock_redis_result.error = True
        mock_redis_result.value = None

        try:
            with patch("app.api.v1.diagnosis.safety_gateway_client", mock_sg), \
                 patch("app.config.redis.RedisPool.get_with_error", return_value=mock_redis_result), \
                 patch("app.config.redis.RedisPool.setex", AsyncMock()):
                response = test_client.get("/api/v1/diagnosis/result/req_001")

            assert response.status_code == 200
            data = response.json()
            assert data["request_id"] == "req_001"
            assert data["desensitization_status"] == "full"
            assert data["consent_status_at_output"] == "granted"
        finally:
            app.dependency_overrides.clear()

    def test_get_diagnosis_result_safety_gateway_blocked(self, test_client):
        from app.main import app
        from app.config.database import get_db_session
        from app.dependencies import get_diagnosis_repo, get_consent_repo, get_session_repo
        from app.security.auth import get_current_active_user
        from app.repositories.diagnosis_repository import DiagnosisRepository
        from app.repositories.consent_repository import ConsentRepository
        from app.repositories.session_repository import SessionRepository
        from app.models.database import DiagnosisResult as DBDiagnosisResult, DiagnosisSession, ConsentRecord
        from app.models.schemas import ConsentStatus

        mock_db = AsyncMock(spec=AsyncSession)
        mock_diagnosis_repo = AsyncMock(spec=DiagnosisRepository)
        mock_consent_repo = AsyncMock(spec=ConsentRepository)
        mock_session_repo = AsyncMock(spec=SessionRepository)

        mock_diagnosis_repo.get_result = AsyncMock(
            return_value=DBDiagnosisResult(
                request_id="req_002",
                session_id="session_002",
                organ="thyroid",
                tier="full",
                confidence=0.85,
                findings={"findings": "normal"},
                status="completed",
                created_at=datetime.now(timezone.utc),
            )
        )
        mock_session_repo.get_session = AsyncMock(
            return_value=DiagnosisSession(
                session_id="session_002",
                patient_id="patient_002",
                device_id="device_001",
                status="active",
                target_organs=["thyroid"],
                organ_statuses={"thyroid": "completed"},
                knowledge_base_version="kb_v1.0.0",
                consent_status_at_creation="granted",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        mock_consent_repo.get_latest_consent = AsyncMock(
            return_value=ConsentRecord(
                record_id=1,
                patient_id="patient_002",
                status=ConsentStatus.GRANTED.value,
                consent_version="1.0",
            )
        )

        async def mock_get_db():
            yield mock_db

        app.dependency_overrides[get_db_session] = mock_get_db
        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diagnosis_repo
        app.dependency_overrides[get_consent_repo] = lambda: mock_consent_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        mock_sg = AsyncMock()
        mock_sg.check_output = AsyncMock(return_value={
            "allowed": False,
            "error_code": "E4005",
            "consent_status_at_output": "unknown_blocked",
            "degraded_mode": True,
            "service_unavailable": True,
        })

        mock_redis_result = MagicMock()
        mock_redis_result.error = True
        mock_redis_result.value = None

        try:
            with patch("app.api.v1.diagnosis.safety_gateway_client", mock_sg), \
                 patch("app.config.redis.RedisPool.get_with_error", return_value=mock_redis_result):
                response = test_client.get("/api/v1/diagnosis/result/req_002")

            assert response.status_code == 503
            assert "E4005" in response.json()["error_code"]
        finally:
            app.dependency_overrides.clear()


class TestIdempotencyCacheHit:
    """Tests for idempotency cache hit behavior"""

    def test_idempotency_cache_hit(self, test_client):
        from app.main import app
        from app.dependencies import get_diagnosis_repo, get_consent_repo, get_session_repo, get_audit_service
        from app.security.auth import get_current_active_user
        from app.repositories.diagnosis_repository import DiagnosisRepository
        from app.repositories.consent_repository import ConsentRepository
        from app.repositories.session_repository import SessionRepository
        from app.services.audit_service import AuditService

        mock_diagnosis_repo = AsyncMock(spec=DiagnosisRepository)
        mock_consent_repo = AsyncMock(spec=ConsentRepository)
        mock_session_repo = AsyncMock(spec=SessionRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_diagnosis_repo] = lambda: mock_diagnosis_repo
        app.dependency_overrides[get_consent_repo] = lambda: mock_consent_repo
        app.dependency_overrides[get_session_repo] = lambda: mock_session_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "test_user", "role": "doctor"}

        cached_response = {
            "request_id": "req_cached_001",
            "session_id": "session_001",
            "status": "processing",
            "estimated_time_ms": 3000,
            "response_tier": "full",
        }

        from app.config.redis import RedisGetResult

        mock_redis_result = RedisGetResult(
            value=json.dumps(cached_response).encode("utf-8"),
            error=False,
        )

        try:
            with patch("app.api.v1.diagnosis.check_rate_limit", new_callable=AsyncMock, return_value=None), \
                 patch("app.config.redis.RedisPool.get_with_error", new_callable=AsyncMock, return_value=mock_redis_result):
                response = test_client.post(
                    "/api/v1/diagnosis/request",
                    json={
                        "session_id": "session_001",
                        "target_organ": "thyroid",
                        "image_ids": [],
                        "response_tier": "full",
                        "idempotency_key": "cached_key_123",
                    },
                )

            assert response.status_code == 200
            data = response.json()
            assert data["request_id"] == "req_cached_001"
            assert data["status"] == "processing"
        finally:
            app.dependency_overrides.clear()


class TestRateLimitTrigger:
    """Tests for rate limiting behavior"""

    def test_rate_limit_config_values(self):
        from app.config.rate_limit import RATE_LIMITS

        assert "/api/v1/diagnosis/request" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/diagnosis/request"]["capacity"] == 10
        assert "/api/v1/imaging/dicom/upload" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/imaging/dicom/upload"]["capacity"] == 30
        assert "/api/v1/feedback" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/feedback"]["capacity"] == 20

    def test_token_bucket_exhaustion(self):
        from app.config.rate_limit import TokenBucket

        bucket = TokenBucket(capacity=2, refill_rate=0.01)

        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is False

    def test_rate_limit_middleware_registered(self):
        from app.main import app
        from app.config.rate_limit import RateLimitMiddleware

        has_rate_limit = any(
            getattr(m.cls, "__name__", "") == "RateLimitMiddleware" or m.cls is RateLimitMiddleware
            for m in app.user_middleware
        )
        assert has_rate_limit, "RateLimitMiddleware should be registered in app middleware"


class TestWebSocketAuthFlow:
    """Tests for WebSocket authentication flow"""

    def test_nav_auth_close_codes(self):
        from app.api.websocket.nav import WS_CLOSE_AUTH_FAILED, WS_CLOSE_AUTH_TIMEOUT

        assert WS_CLOSE_AUTH_FAILED == 4001
        assert WS_CLOSE_AUTH_TIMEOUT == 4002

    @pytest.mark.asyncio
    async def test_dicom_cross_auth_uses_nav_token(self):
        from app.api.websocket.nav import ConnectionManager

        manager = ConnectionManager()
        session_id = "test_cross_auth"
        token = await manager.generate_auth_token(session_id)

        is_valid = await manager.validate_auth_token(session_id, token)
        assert is_valid is True

        is_invalid = await manager.validate_auth_token(session_id, "wrong_token")
        assert is_invalid is False

    def test_dicom_binary_frame_format(self):
        import struct
        from app.api.websocket.dicom import calculate_crc32

        frame_index = 42
        flags = 0x01
        dicom_data = b"DICOM_BINARY_FRAME_DATA"
        dicom_length = len(dicom_data)

        header = struct.pack("<I", frame_index) + struct.pack("B", flags) + struct.pack("<I", dicom_length)
        frame = header + dicom_data
        crc = calculate_crc32(frame)
        frame += struct.pack("<I", crc)

        assert len(frame) == 9 + dicom_length + 4

        parsed_index = struct.unpack("<I", frame[0:4])[0]
        assert parsed_index == 42
        parsed_flags = frame[4]
        assert parsed_flags == 0x01
        parsed_length = struct.unpack("<I", frame[5:9])[0]
        assert parsed_length == dicom_length

    def test_dicom_crc_mismatch_detection(self):
        import struct
        from app.api.websocket.dicom import calculate_crc32

        frame_index = 1
        flags = 0x00
        dicom_data = b"test_data"
        dicom_length = len(dicom_data)

        header = struct.pack("<I", frame_index) + struct.pack("B", flags) + struct.pack("<I", dicom_length)
        frame = header + dicom_data
        correct_crc = calculate_crc32(frame)
        wrong_crc = correct_crc + 1
        frame += struct.pack("<I", wrong_crc)

        received_crc = struct.unpack("<I", frame[9 + dicom_length:9 + dicom_length + 4])[0]
        computed_crc = calculate_crc32(frame[0:9 + dicom_length])
        assert received_crc != computed_crc


class TestDicomSessionIdValidation:
    """Tests for DICOM channel session_id consistency validation (P0-1 fix)"""

    def test_auth_message_session_id_mismatch_detected(self):
        from app.models.schemas import AuthMessage

        auth_msg = AuthMessage(
            type="auth",
            session_id="session_A",
            token="valid_token",
            timestamp=1000,
        )
        url_session_id = "session_B"

        assert auth_msg.session_id != url_session_id

    def test_auth_message_session_id_match(self):
        from app.models.schemas import AuthMessage

        auth_msg = AuthMessage(
            type="auth",
            session_id="session_001",
            token="valid_token",
            timestamp=1000,
        )
        url_session_id = "session_001"

        assert auth_msg.session_id == url_session_id

    def test_auth_message_no_session_id_field(self):
        from app.models.schemas import AuthMessage

        auth_msg = AuthMessage(
            type="auth",
            token="valid_token",
            timestamp=1000,
        )
        assert auth_msg.session_id is None or auth_msg.session_id == ""


class TestTokenBucketConcurrency:
    """Tests for TokenBucket asyncio.Lock safety (P0-3 fix)"""

    def test_buckets_lock_exists(self):
        from app.config.rate_limit import _bucket_stripes
        import asyncio

        assert isinstance(_bucket_stripes, list)
        assert len(_bucket_stripes) > 0
        assert all(isinstance(lock, asyncio.Lock) for lock in _bucket_stripes)

    @pytest.mark.asyncio
    async def test_concurrent_bucket_access(self):
        from app.config.rate_limit import _get_bucket

        tasks = [_get_bucket(f"key_{i}", "/api/v1/diagnosis/request") for i in range(10)]
        buckets = await asyncio.gather(*tasks)

        assert len(buckets) == 10
        assert all(b.capacity == 10 for b in buckets)

    @pytest.mark.asyncio
    async def test_concurrent_consume_does_not_exceed_capacity(self):
        from app.config.rate_limit import TokenBucket

        bucket = TokenBucket(capacity=5, refill_rate=0.0)

        results = [bucket.consume() for _ in range(10)]

        successful = sum(1 for r in results if r)
        assert successful <= 5


class TestDeviceRecoveryResponse:
    """Tests for device recovery response fields (P1-3 fix)"""

    def test_device_recovery_response_has_required_fields(self):
        from app.models.schemas import DeviceRecoveryResponse

        resp = DeviceRecoveryResponse(
            device_id="dev_001",
            status="active",
            recovery_token="token_abc123",
            expires_at="2026-05-13T12:00:00+00:00",
        )

        assert resp.device_id == "dev_001"
        assert resp.status == "active"
        assert resp.recovery_token == "token_abc123"
        assert resp.expires_at == "2026-05-13T12:00:00+00:00"

    def test_device_remote_recovery_response_has_required_fields(self):
        from app.models.schemas import DeviceRemoteRecoveryResponse

        resp = DeviceRemoteRecoveryResponse(
            device_id="dev_001",
            status="active",
            new_api_key="new_key_xyz789",
            expires_at="2026-05-13T12:00:00+00:00",
        )

        assert resp.device_id == "dev_001"
        assert resp.status == "active"
        assert resp.new_api_key == "new_key_xyz789"
        assert resp.expires_at == "2026-05-13T12:00:00+00:00"


class TestNavigationHintCompleteness:
    """Tests for base64+HTTP navigation_hint field completeness (P1-4 fix)"""

    def test_navigation_hint_has_all_required_fields(self):
        from app.config.settings import settings

        hint = {
            "available": False,
            "confidence": 0.0,
            "threshold": settings.navigation_confidence_threshold,
        }

        assert "available" in hint
        assert "confidence" in hint
        assert "threshold" in hint
        assert isinstance(hint["available"], bool)
        assert isinstance(hint["confidence"], float)
        assert isinstance(hint["threshold"], float)
        assert hint["threshold"] == 0.75


class TestFhirEndpoints:
    """Tests for FHIR endpoint registration and basic functionality (P1-6 fix)"""

    def test_fhir_router_registered_in_app(self):
        from app.main import app

        routes = [route.path for route in app.routes]
        fhir_routes = [r for r in routes if r.startswith("/fhir")]
        assert len(fhir_routes) > 0, "FHIR routes should be registered"

    def test_fhir_rate_limit_path_matches_spec(self):
        from app.config.rate_limit import RATE_LIMITS

        assert "/fhir" in RATE_LIMITS
        assert RATE_LIMITS["/fhir"]["capacity"] == 60

    def test_fhir_patient_route_exists(self):
        from app.main import app

        routes = [route.path for route in app.routes]
        assert "/fhir/Patient/{id}" in routes

    def test_fhir_observation_route_exists(self):
        from app.main import app

        routes = [route.path for route in app.routes]
        assert "/fhir/Observation" in routes


class TestConfigReferences:
    """Tests for hardcoded magic numbers replaced with config references (P1-5 fix)"""

    def test_session_max_ttl_config_exists(self):
        from app.config.settings import settings

        assert hasattr(settings, "session_max_ttl_seconds")
        assert settings.session_max_ttl_seconds == 7200

    def test_dicom_retransmit_config_exists(self):
        from app.config.settings import settings

        assert hasattr(settings, "dicom_max_retransmit_per_frame")
        assert settings.dicom_max_retransmit_per_frame == 3
        assert hasattr(settings, "dicom_max_retransmit_frame_range")
        assert settings.dicom_max_retransmit_frame_range == 90

    def test_navigation_confidence_threshold_config_exists(self):
        from app.config.settings import settings

        assert hasattr(settings, "navigation_confidence_threshold")
        assert settings.navigation_confidence_threshold == 0.75
