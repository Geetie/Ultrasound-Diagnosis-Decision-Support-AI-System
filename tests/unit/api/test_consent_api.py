from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import ConsentAction, ConsentStatus

pytestmark = [pytest.mark.track_api, pytest.mark.track_sec, pytest.mark.sec_domain_consent]


class TestGetConsent:
    """GET /api/v1/consent/{patient_id} 测试"""

    @pytest.mark.asyncio
    async def test_get_consent_granted(self):
        from app.api.v1.consent import get_consent

        mock_db = AsyncMock()
        mock_record = MagicMock()
        mock_record.patient_id = "P001"
        mock_record.status = ConsentStatus.GRANTED.value
        mock_record.granted_at = datetime.now(timezone.utc)
        mock_record.withdrawn_at = None
        mock_record.consent_version = "1.0"

        with patch("app.api.v1.consent.ConsentRepository") as MockRepo:
            MockRepo.return_value.get_latest_consent = AsyncMock(return_value=mock_record)
            result = await get_consent(
                patient_id="P001",
                db=mock_db,
                current_user={"user_id": "admin", "role": "SYSTEM_ADMIN"},
            )

        assert result.consent_status == ConsentStatus.GRANTED

    @pytest.mark.asyncio
    async def test_get_consent_not_found_returns_denied(self):
        from app.api.v1.consent import get_consent

        mock_db = AsyncMock()

        with patch("app.api.v1.consent.ConsentRepository") as MockRepo:
            MockRepo.return_value.get_latest_consent = AsyncMock(return_value=None)
            result = await get_consent(
                patient_id="P002",
                db=mock_db,
                current_user={"user_id": "admin", "role": "SYSTEM_ADMIN"},
            )

        assert result.consent_status == ConsentStatus.DENIED
        assert result.patient_id == "P002"

    @pytest.mark.asyncio
    async def test_get_consent_withdrawn_returns_withdrawn_status(self):
        from app.api.v1.consent import get_consent

        mock_db = AsyncMock()
        mock_record = MagicMock()
        mock_record.patient_id = "P003"
        mock_record.status = ConsentStatus.WITHDRAWN.value
        mock_record.granted_at = datetime.now(timezone.utc)
        mock_record.withdrawn_at = datetime.now(timezone.utc)
        mock_record.consent_version = "1.0"

        with patch("app.api.v1.consent.ConsentRepository") as MockRepo:
            MockRepo.return_value.get_latest_consent = AsyncMock(return_value=mock_record)
            result = await get_consent(
                patient_id="P003",
                db=mock_db,
                current_user={"user_id": "admin", "role": "SYSTEM_ADMIN"},
            )

        assert result.consent_status == ConsentStatus.WITHDRAWN

    @pytest.mark.asyncio
    async def test_get_consent_privileged_role_allowed(self):
        """特权角色可查询任意患者"""
        from app.api.v1.consent import get_consent

        mock_db = AsyncMock()
        mock_record = MagicMock()
        mock_record.patient_id = "P004"
        mock_record.status = ConsentStatus.GRANTED.value
        mock_record.granted_at = datetime.now(timezone.utc)
        mock_record.withdrawn_at = None
        mock_record.consent_version = "1.0"

        for role in ["SYSTEM_ADMIN", "QUALITY_CONTROL", "DEPARTMENT_HEAD"]:
            with patch("app.api.v1.consent.ConsentRepository") as MockRepo:
                MockRepo.return_value.get_latest_consent = AsyncMock(return_value=mock_record)
                result = await get_consent(
                    patient_id="P004",
                    db=mock_db,
                    current_user={"user_id": "user1", "role": role},
                )
            assert result.consent_status == ConsentStatus.GRANTED


class TestUpdateConsent:
    """POST /api/v1/consent 测试"""

    @pytest.mark.asyncio
    async def test_update_consent_grant(self):
        """创建同意记录成功"""
        from app.api.v1.consent import update_consent
        from app.models.schemas import ConsentUpdateRequest

        mock_db = AsyncMock()
        mock_record = MagicMock()
        mock_record.patient_id = "P001"
        mock_record.status = ConsentStatus.GRANTED.value

        with patch("app.api.v1.consent.ConsentRepository") as MockRepo, \
             patch("app.api.v1.consent.record_hsm_degradation_if_needed", new_callable=AsyncMock, return_value=False):
            MockRepo.return_value.create_consent = AsyncMock(return_value=mock_record)
            result = await update_consent(
                request=ConsentUpdateRequest(
                    patient_id="P001",
                    action=ConsentAction.GRANT,
                    operator_id="admin",
                    consent_version="1.0",
                ),
                db=mock_db,
                current_user={"user_id": "admin", "role": "SYSTEM_ADMIN"},
                _={"user_id": "admin", "role": "SYSTEM_ADMIN"},
            )

        assert result.consent_status == ConsentStatus.GRANTED

    @pytest.mark.asyncio
    async def test_update_consent_withdraw(self):
        """撤回同意成功"""
        from app.api.v1.consent import update_consent
        from app.models.schemas import ConsentUpdateRequest

        mock_db = AsyncMock()
        mock_record = MagicMock()
        mock_record.patient_id = "P001"
        mock_record.status = ConsentStatus.WITHDRAWN.value

        with patch("app.api.v1.consent.ConsentRepository") as MockRepo, \
             patch("app.api.v1.consent.record_hsm_degradation_if_needed", new_callable=AsyncMock, return_value=False):
            MockRepo.return_value.create_consent = AsyncMock(return_value=mock_record)
            result = await update_consent(
                request=ConsentUpdateRequest(
                    patient_id="P001",
                    action=ConsentAction.WITHDRAW,
                    operator_id="admin",
                    consent_version="1.0",
                ),
                db=mock_db,
                current_user={"user_id": "admin", "role": "SYSTEM_ADMIN"},
                _={"user_id": "admin", "role": "SYSTEM_ADMIN"},
            )

        assert result.consent_status == ConsentStatus.WITHDRAWN

    @pytest.mark.asyncio
    async def test_update_consent_invalid_action(self):
        """无效操作类型被Pydantic枚举验证拒绝"""
        from pydantic import ValidationError

        from app.models.schemas import ConsentUpdateRequest

        with pytest.raises(ValidationError):
            ConsentUpdateRequest(
                patient_id="P001",
                action="invalid_action",
                consent_version="1.0",
            )
