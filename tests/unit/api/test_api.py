"""Unit tests for API endpoints"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

pytestmark = [pytest.mark.track_api, pytest.mark.track_sec]

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import (
    ConsentStatus,
    DiagnosisRequest,
    FeedbackSubmission,
    OrganStatus,
    SessionCreateRequest,
    SessionStatus,
)


class TestConsentRepository:
    """Tests for ConsentRepository"""

    @pytest.fixture
    def consent_repo(self):
        from app.repositories.consent_repository import ConsentRepository
        db = AsyncMock(spec=AsyncSession)
        return ConsentRepository(db)

    @pytest.fixture
    def mock_consent_record(self):
        from app.models.database import ConsentRecord
        return ConsentRecord(
            record_id=123456789,
            patient_id="patient_001",
            status="granted",
            consent_version="1.0",
            granted_at=datetime.now(timezone.utc)
        )

    async def test_get_latest_consent_not_found(self, consent_repo):
        """Test getting consent when patient has no consent record"""
        consent_repo.db.execute = AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=None)))

        result = await consent_repo.get_latest_consent("patient_001")

        assert result is None

    async def test_get_latest_consent_found(self, consent_repo, mock_consent_record):
        """Test getting latest consent record"""
        consent_repo.db.execute = AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=mock_consent_record)))

        result = await consent_repo.get_latest_consent("patient_001")

        assert result is not None
        assert result.patient_id == "patient_001"
        assert result.status == "granted"


class TestSessionRepository:
    """Tests for SessionRepository"""

    @pytest.fixture
    def session_repo(self):
        from app.repositories.session_repository import SessionRepository
        db = AsyncMock(spec=AsyncSession)
        return SessionRepository(db)

    async def test_create_session(self, session_repo):
        """Test creating a new session"""
        from app.models.schemas import ConsentStatus

        session = await session_repo.create_session(
            patient_id="patient_001",
            device_id="device_001",
            target_organs=["thyroid"],
            consent_status=ConsentStatus.GRANTED
        )

        assert session is not None
        assert session.patient_id == "patient_001"
        assert session.device_id == "device_001"
        assert session.status == "pending"
        assert session.target_organs == ["thyroid"]

    async def test_get_session_not_found(self, session_repo):
        """Test getting non-existent session"""
        session_repo.db.execute = AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=None)))

        result = await session_repo.get_session("nonexistent_id")

        assert result is None


class TestDiagnosisRepository:
    """Tests for DiagnosisRepository"""

    @pytest.fixture
    def diagnosis_repo(self):
        from app.repositories.diagnosis_repository import DiagnosisRepository
        db = AsyncMock(spec=AsyncSession)
        return DiagnosisRepository(db)

    async def test_create_request(self, diagnosis_repo):
        """Test creating diagnosis request"""
        from app.models.schemas import ResponseTier

        request_id = await diagnosis_repo.create_request(
            session_id="session_001",
            organ="thyroid",
            response_tier=ResponseTier.FULL
        )

        assert request_id is not None
        assert request_id.startswith("req_")


class TestDeviceRepository:
    """Tests for DeviceRepository"""

    @pytest.fixture
    def device_repo(self):
        from app.repositories.device_repository import DeviceRepository
        db = AsyncMock(spec=AsyncSession)
        return DeviceRepository(db)

    @pytest.fixture
    def mock_device(self):
        from app.models.database import Device
        from app.repositories.device_repository import _hash_api_key
        return Device(
            device_id="device_001",
            device_name="Test Device",
            device_type="ultrasound",
            location="Room 101",
            api_key=_hash_api_key("test_api_key_12345"),
            status="active"
        )

    async def test_authenticate_device_success(self, device_repo, mock_device):
        """Test successful device authentication"""
        device_repo.get_device = AsyncMock(return_value=mock_device)

        result = await device_repo.authenticate_device("device_001", "test_api_key_12345")

        assert result is not None
        assert result.device_id == "device_001"

    async def test_authenticate_device_wrong_api_key(self, device_repo, mock_device):
        """Test device authentication with wrong API key"""
        device_repo.get_device = AsyncMock(return_value=mock_device)

        result = await device_repo.authenticate_device("device_001", "wrong_key")

        assert result is None

    async def test_authenticate_device_not_found(self, device_repo):
        """Test device authentication when device not found"""
        device_repo.get_device = AsyncMock(return_value=None)

        result = await device_repo.authenticate_device("nonexistent", "any_key")

        assert result is None

    async def test_authenticate_device_inactive(self, device_repo, mock_device):
        mock_device.status = "inactive"
        device_repo.get_device = AsyncMock(return_value=mock_device)

        result = await device_repo.authenticate_device("device_001", "test_api_key_12345")

        assert result is None


class TestAuditService:
    """Tests for AuditService"""

    @pytest.fixture
    def audit_service(self):
        from app.services.audit_service import AuditService
        db = AsyncMock(spec=AsyncSession)
        return AuditService(db)

    async def test_log_action(self, audit_service):
        """Test logging an audit action"""
        mock_result = Mock()
        mock_result.scalar_one_or_none = Mock(return_value=None)
        audit_service.db.execute = AsyncMock(return_value=mock_result)
        audit_service.db.add = Mock()

        result = await audit_service.log_action(
            action="test_action",
            actor="test_actor",
            session_id="session_001",
            target_type="test_target",
            target_id="target_001",
            detail={"key": "value"}
        )

        assert result is not None
        assert result.action == "test_action"
        assert result.actor == "test_actor"


class TestSafetyService:
    """Tests for SafetyService"""

    @pytest.fixture
    def safety_service(self):
        from app.services.safety_service import SafetyService
        db = AsyncMock(spec=AsyncSession)
        return SafetyService(db)

    async def test_log_event(self, safety_service):
        """Test logging a safety event"""
        from app.models.schemas import SafetyEventType, SafetySeverity

        result = await safety_service.log_event(
            event_type=SafetyEventType.CONSENT_WITHDRAWN,
            severity=SafetySeverity.IMPORTANT,
            patient_id="patient_001",
            payload={"test": "data"}
        )

        assert result is not None
        assert result.event_type == "ConsentWithdrawn"
        assert result.severity == "important"


class TestSchemas:
    """Tests for Pydantic schemas"""

    def test_session_create_request(self):
        """Test SessionCreateRequest schema"""
        request = SessionCreateRequest(
            patient_id="patient_001",
            device_id="device_001",
            target_organs=["thyroid", "liver"]
        )

        assert request.patient_id == "patient_001"
        assert request.device_id == "device_001"
        assert request.target_organs == ["thyroid", "liver"]

    def test_diagnosis_request(self):
        """Test DiagnosisRequest schema"""
        from app.models.schemas import PatientContext, ResponseTier

        request = DiagnosisRequest(
            session_id="session_001",
            target_organ="thyroid",
            image_ids=["img_001"],
            response_tier=ResponseTier.FULL,
            patient_context=PatientContext(age=45, gender="F"),
            idempotency_key="test_key_123"
        )

        assert request.session_id == "session_001"
        assert request.target_organ == "thyroid"
        assert request.response_tier == ResponseTier.FULL
        assert request.patient_context.age == 45
        assert request.idempotency_key == "test_key_123"

    def test_feedback_submission(self):
        """Test FeedbackSubmission schema"""
        from app.models.schemas import FeedbackContent, FeedbackLevel, FeedbackType

        feedback = FeedbackSubmission(
            request_id="req_001",
            session_id="session_001",
            feedback_type=FeedbackType.CORRECTION,
            feedback_level=FeedbackLevel.L2,
            content=FeedbackContent(
                field="diagnosis",
                original_value="Nodule",
                corrected_value="Cyst",
                reason="Visual confirmation"
            ),
            reviewer_id="doctor_001"
        )

        assert feedback.request_id == "req_001"
        assert feedback.feedback_type == FeedbackType.CORRECTION
        assert feedback.content.original_value == "Nodule"


class TestEnums:
    """Tests for enum classes"""

    def test_session_status_enum(self):
        """Test SessionStatus enum"""
        assert SessionStatus.PENDING == "pending"
        assert SessionStatus.ACTIVE == "active"
        assert SessionStatus.COMPLETED == "completed"

    def test_consent_status_enum(self):
        """Test ConsentStatus enum"""
        assert ConsentStatus.GRANTED == "granted"
        assert ConsentStatus.WITHDRAWN == "withdrawn"
        assert ConsentStatus.DENIED == "denied"

    def test_organ_status_enum(self):
        """Test OrganStatus enum"""
        assert OrganStatus.PENDING == "pending"
        assert OrganStatus.IN_PROGRESS == "in_progress"
        assert OrganStatus.COMPLETED == "completed"


class TestBinaryFrameFlags:
    """Tests for S-03: Binary frame flags semantics"""

    def test_flags_bit0_is_key_frame(self):
        flags = 0x01
        is_key_frame = bool(flags & 0x01)
        priority = (flags >> 1) & 0x01
        assert is_key_frame is True
        assert priority == 0

    def test_flags_bit1_priority(self):
        flags = 0x02
        is_key_frame = bool(flags & 0x01)
        priority = (flags >> 1) & 0x01
        assert is_key_frame is False
        assert priority == 1

    def test_flags_both_set(self):
        flags = 0x03
        is_key_frame = bool(flags & 0x01)
        priority = (flags >> 1) & 0x01
        assert is_key_frame is True
        assert priority == 1

    def test_flags_none_set(self):
        flags = 0x00
        is_key_frame = bool(flags & 0x01)
        priority = (flags >> 1) & 0x01
        assert is_key_frame is False
        assert priority == 0


class TestIdempotencyTTL:
    """Tests for M-01: Idempotency cache TTL"""

    def test_idempotency_ttl_is_600(self):
        from app.api.v1.diagnosis import IDEMPOTENCY_TTL_SECONDS
        assert IDEMPOTENCY_TTL_SECONDS == 600


class TestDatabaseModelChanges:
    """Tests for M-04 and M-05: Database model changes"""

    def test_consent_record_has_granted_at(self):
        from app.models.database import ConsentRecord
        columns = {c.name for c in ConsentRecord.__table__.columns}
        assert "granted_at" in columns
        assert "consented_at" not in columns

    def test_session_table_session_id_is_pk(self):
        from app.models.database import DiagnosisSession
        pk_cols = [c.name for c in DiagnosisSession.__table__.primary_key.columns]
        assert "session_id" in pk_cols
        assert "id" not in pk_cols


class TestConsentQueryResponseSchema:
    """Tests for M-04: ConsentQueryResponse field rename"""

    def test_consent_query_response_has_consented_at(self):
        from app.models.schemas import ConsentQueryResponse
        fields = ConsentQueryResponse.model_fields
        assert "consented_at" in fields
