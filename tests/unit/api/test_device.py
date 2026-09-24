from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_api, pytest.mark.track_sec]


class TestDeviceRegister:
    """POST /api/v1/admin/device/register"""

    @pytest.fixture
    def mock_deps(self):
        from app.dependencies import get_audit_service, get_device_repo
        from app.main import app
        from app.repositories.device_repository import DeviceRepository
        from app.security.auth import get_current_active_user
        from app.services.audit_service import AuditService

        mock_device_repo = AsyncMock(spec=DeviceRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_device_repo] = lambda: mock_device_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "admin", "role": "SYSTEM_ADMIN"}

        yield {
            "device_repo": mock_device_repo,
            "audit_service": mock_audit_service,
        }

        app.dependency_overrides.clear()

    def test_register_success(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_20260516_abc12345"
        mock_device.status = "active"

        mock_deps["device_repo"].get_device_by_name = AsyncMock(return_value=None)
        mock_deps["device_repo"].register_device = AsyncMock(
            return_value=(mock_device, "ak_raw_api_key_12345")
        )
        mock_deps["audit_service"].log_action = AsyncMock()

        response = test_client.post(
            "/api/v1/admin/device/register",
            json={
                "device_name": "ultrasound-probe-01",
                "device_type": "portable",
                "location": "room-301",
                "jetpack_version": "1.0.0",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "dev_20260516_abc12345"
        assert data["api_key"] == "ak_raw_api_key_12345"
        assert data["status"] == "active"

    def test_register_deregistered_name_returns_400(self, test_client, mock_deps):
        mock_existing = MagicMock()
        mock_existing.status = "deregistered"

        mock_deps["device_repo"].get_device_by_name = AsyncMock(return_value=mock_existing)

        response = test_client.post(
            "/api/v1/admin/device/register",
            json={
                "device_name": "ultrasound-probe-01",
                "device_type": "portable",
                "location": "room-301",
                "jetpack_version": "1.0.0",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "E1001"

    def test_register_requires_system_admin(self, test_client, mock_deps):
        from app.main import app
        from app.security.auth import get_current_active_user

        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor1", "role": "DOCTOR"}

        response = test_client.post(
            "/api/v1/admin/device/register",
            json={
                "device_name": "ultrasound-probe-01",
                "device_type": "portable",
                "location": "room-301",
                "jetpack_version": "1.0.0",
            },
        )

        assert response.status_code == 403


class TestDeviceDeregister:
    """POST /api/v1/admin/device/{device_id}/deregister"""

    @pytest.fixture
    def mock_deps(self):
        from app.dependencies import get_audit_service, get_device_repo
        from app.main import app
        from app.repositories.device_repository import DeviceRepository
        from app.security.auth import get_current_active_user
        from app.services.audit_service import AuditService

        mock_device_repo = AsyncMock(spec=DeviceRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_device_repo] = lambda: mock_device_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "admin", "role": "SYSTEM_ADMIN"}

        yield {
            "device_repo": mock_device_repo,
            "audit_service": mock_audit_service,
        }

        app.dependency_overrides.clear()

    def test_deregister_success(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_001"
        mock_device.status = "active"

        mock_updated = MagicMock()
        mock_updated.status = "deregistered"

        mock_deps["device_repo"].get_device = AsyncMock(return_value=mock_device)
        mock_deps["device_repo"].update_device_status = AsyncMock(return_value=mock_updated)
        mock_deps["audit_service"].log_action = AsyncMock()

        response = test_client.post("/api/v1/admin/device/dev_001/deregister")

        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "dev_001"
        assert data["status"] == "deregistered"

    def test_deregister_already_deregistered_returns_400(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_001"
        mock_device.status = "deregistered"

        mock_deps["device_repo"].get_device = AsyncMock(return_value=mock_device)

        response = test_client.post("/api/v1/admin/device/dev_001/deregister")

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "E1001"

    def test_deregister_not_found_returns_404(self, test_client, mock_deps):
        mock_deps["device_repo"].get_device = AsyncMock(return_value=None)

        response = test_client.post("/api/v1/admin/device/dev_nonexistent/deregister")

        assert response.status_code == 404
        data = response.json()
        assert data["error_code"] == "E1002"

    def test_deregister_requires_system_admin(self, test_client, mock_deps):
        from app.main import app
        from app.security.auth import get_current_active_user

        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor1", "role": "DOCTOR"}

        response = test_client.post("/api/v1/admin/device/dev_001/deregister")

        assert response.status_code == 403


class TestDeviceRecovery:
    """POST /api/v1/admin/device/{device_id}/recovery"""

    @pytest.fixture
    def mock_deps(self):
        from app.dependencies import get_audit_service, get_device_repo
        from app.main import app
        from app.repositories.device_repository import DeviceRepository
        from app.security.auth import get_current_active_user
        from app.services.audit_service import AuditService

        mock_device_repo = AsyncMock(spec=DeviceRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_device_repo] = lambda: mock_device_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "admin", "role": "SYSTEM_ADMIN"}

        yield {
            "device_repo": mock_device_repo,
            "audit_service": mock_audit_service,
        }

        app.dependency_overrides.clear()

    def test_recovery_quarantined_device_success(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_001"
        mock_device.status = "quarantined"

        mock_updated = MagicMock()
        mock_updated.status = "active"

        mock_deps["device_repo"].get_device = AsyncMock(return_value=mock_device)
        mock_deps["device_repo"].update_device_status = AsyncMock(return_value=mock_updated)
        mock_deps["device_repo"].store_recovery_token = AsyncMock()
        mock_deps["audit_service"].log_action = AsyncMock()

        response = test_client.post("/api/v1/admin/device/dev_001/recovery")

        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "dev_001"
        assert data["status"] == "active"
        assert "recovery_token" in data
        assert "expires_at" in data

    def test_recovery_active_device_returns_400(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_001"
        mock_device.status = "active"

        mock_deps["device_repo"].get_device = AsyncMock(return_value=mock_device)

        response = test_client.post("/api/v1/admin/device/dev_001/recovery")

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "E1001"

    def test_recovery_deregistered_device_returns_400(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_001"
        mock_device.status = "deregistered"

        mock_deps["device_repo"].get_device = AsyncMock(return_value=mock_device)

        response = test_client.post("/api/v1/admin/device/dev_001/recovery")

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "E1001"

    def test_recovery_requires_system_admin(self, test_client, mock_deps):
        from app.main import app
        from app.security.auth import get_current_active_user

        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor1", "role": "DOCTOR"}

        response = test_client.post("/api/v1/admin/device/dev_001/recovery")

        assert response.status_code == 403


class TestDeviceRemoteRecovery:
    """POST /api/v1/admin/device/{device_id}/remote-recovery"""

    @pytest.fixture
    def mock_deps(self):
        from app.dependencies import get_audit_service, get_device_repo
        from app.main import app
        from app.repositories.device_repository import DeviceRepository
        from app.security.auth import get_current_active_user
        from app.services.audit_service import AuditService

        mock_device_repo = AsyncMock(spec=DeviceRepository)
        mock_audit_service = AsyncMock(spec=AuditService)

        app.dependency_overrides[get_device_repo] = lambda: mock_device_repo
        app.dependency_overrides[get_audit_service] = lambda: mock_audit_service
        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "admin", "role": "SYSTEM_ADMIN"}

        yield {
            "device_repo": mock_device_repo,
            "audit_service": mock_audit_service,
        }

        app.dependency_overrides.clear()

    def test_remote_recovery_quarantined_device_success(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_001"
        mock_device.status = "quarantined"

        mock_updated = MagicMock()
        mock_updated.status = "active"

        mock_deps["device_repo"].get_device = AsyncMock(return_value=mock_device)
        mock_deps["device_repo"].update_device_status = AsyncMock(return_value=mock_updated)
        mock_deps["device_repo"].store_new_api_key = AsyncMock()
        mock_deps["audit_service"].log_action = AsyncMock()

        response = test_client.post("/api/v1/admin/device/dev_001/remote-recovery")

        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "dev_001"
        assert data["status"] == "active"
        assert "new_api_key" in data
        assert "expires_at" in data

    def test_remote_recovery_non_quarantined_returns_400(self, test_client, mock_deps):
        mock_device = MagicMock()
        mock_device.device_id = "dev_001"
        mock_device.status = "active"

        mock_deps["device_repo"].get_device = AsyncMock(return_value=mock_device)

        response = test_client.post("/api/v1/admin/device/dev_001/remote-recovery")

        assert response.status_code == 400
        data = response.json()
        assert data["error_code"] == "E1001"

    def test_remote_recovery_requires_system_admin(self, test_client, mock_deps):
        from app.main import app
        from app.security.auth import get_current_active_user

        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "doctor1", "role": "DOCTOR"}

        response = test_client.post("/api/v1/admin/device/dev_001/remote-recovery")

        assert response.status_code == 403
