"""A.50.8 E7：OTA 包分发落地（原显式 501 契约位 → 真分发）。

契约（edge/ota_client.py）：GET /api/v1/admin/device/ota/{package}/{version}，
X-Device 头认证，200 二进制流 / 206 Range 续传 / 404 包不存在；
``settings.ota_package_dir`` 未配置时保持 501（部署可见性）。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.track_api, pytest.mark.track_sec]


@pytest.fixture
def ota_env(tmp_path, monkeypatch):
    """造包目录 + 打开 settings 开关 + 注册一台设备头。"""
    from app.config.settings import settings
    from app.dependencies import get_audit_service, get_device_repo
    from app.main import app
    from app.repositories.device_repository import DeviceRepository
    from app.security.auth import get_current_active_user
    from app.services.audit_service import AuditService

    base = tmp_path / "ota_packages"
    pkg_dir = base / "edge-model" / "1.0.1"
    pkg_dir.mkdir(parents=True)
    content = b"A" * 1024
    (pkg_dir / "edge-model-1.0.1.bin").write_bytes(content)

    mock_repo = AsyncMock(spec=DeviceRepository)
    device = AsyncMock()
    device.device_id = "dev_ota_1"
    device.api_key = "ak_x"
    device.status = "active"
    mock_repo.get_device = AsyncMock(return_value=device)
    mock_repo.authenticate_device = AsyncMock(return_value=device)

    app.dependency_overrides[get_device_repo] = lambda: mock_repo
    app.dependency_overrides[get_audit_service] = lambda: AsyncMock(spec=AuditService)
    app.dependency_overrides[get_current_active_user] = lambda: {
        "user_id": "admin", "role": "SYSTEM_ADMIN"
    }
    monkeypatch.setattr(settings, "ota_package_dir", str(base))

    yield {"repo": mock_repo, "content": content, "device": device}
    app.dependency_overrides.clear()


class TestOtaDistribution:
    def _headers(self, token: str = "ak_x") -> dict[str, str]:
        return {"X-Device-Id": "dev_ota_1", "X-Device-Token": token}

    def test_download_200(self, test_client, ota_env):
        r = test_client.get(
            "/api/v1/admin/device/ota/edge-model/1.0.1", headers=self._headers()
        )
        assert r.status_code == 200
        assert r.content == ota_env["content"]
        assert "octet-stream" in r.headers["content-type"]

    def test_range_request_206(self, test_client, ota_env):
        """edge 断点续传：Range → 206 部分内容。"""
        r = test_client.get(
            "/api/v1/admin/device/ota/edge-model/1.0.1",
            headers={**self._headers(), "Range": "bytes=0-99"},
        )
        assert r.status_code == 206
        assert len(r.content) == 100

    def test_missing_package_404(self, test_client, ota_env):
        r = test_client.get(
            "/api/v1/admin/device/ota/edge-model/9.9.9", headers=self._headers()
        )
        assert r.status_code == 404

    def test_unconfigured_dir_keeps_501(self, test_client, ota_env, monkeypatch):
        """目录未配置 → 保持 501 契约位（部署可见性）。"""
        from app.config.settings import settings

        monkeypatch.setattr(settings, "ota_package_dir", "")
        r = test_client.get(
            "/api/v1/admin/device/ota/edge-model/1.0.1", headers=self._headers()
        )
        assert r.status_code == 501

    def test_bad_device_key_401(self, test_client, ota_env):
        # authenticate_device 返回 None（错误凭据）→ 401
        ota_env["repo"].authenticate_device = AsyncMock(return_value=None)
        r = test_client.get(
            "/api/v1/admin/device/ota/edge-model/1.0.1",
            headers=self._headers("ak_wrong"),
        )
        assert r.status_code == 401
