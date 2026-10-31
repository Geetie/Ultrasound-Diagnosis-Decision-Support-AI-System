"""接线审计修复回归测试（wiring-audit-report.md P0-2/3/5）。

覆盖：
- P0-2: POST /api/v1/auth/nav-token JWT→nav token 交换端点
- P0-3: consent 撤回不再双投递（唯一生产者 = ConsentRepository）
- P0-5: AlertEscalationManager start/stop 生命周期可驱动
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_api]


# ----------------------------------------------------------------------
# P0-2 nav token 交换端点
# ----------------------------------------------------------------------


class TestNavTokenExchange:
    @pytest.fixture(autouse=True)
    def _user(self):
        from app.main import app
        from app.security.auth import get_current_active_user

        self._app = app
        app.dependency_overrides[get_current_active_user] = lambda: {
            "user_id": "doctor_001",
            "username": "doctor_001",
            "role": "ULTRASOUND_DOCTOR",
        }
        yield
        app.dependency_overrides.pop(get_current_active_user, None)

    @pytest.mark.asyncio
    async def test_exchange_returns_nav_token(self):
        from app.api.v1.auth import NavTokenExchangeRequest, exchange_nav_token
        from app.api.websocket import nav as nav_mod

        with patch.object(
            nav_mod.manager, "generate_auth_token", AsyncMock(return_value="tok123")
        ) as mock_gen:
            resp = await exchange_nav_token(
                request=NavTokenExchangeRequest(session_id="sess-001"),
                current_user={"user_id": "doctor_001", "username": "doctor_001"},
            )
        mock_gen.assert_awaited_once_with("sess-001")
        assert resp["nav_token"] == "tok123"
        assert resp["session_id"] == "sess-001"
        assert resp["token_type"] == "nav_ws"
        assert resp["expires_in"] > 0

    @pytest.mark.asyncio
    async def test_exchange_rejects_empty_session_id(self):
        from fastapi import HTTPException

        from app.api.v1.auth import NavTokenExchangeRequest, exchange_nav_token

        with pytest.raises(HTTPException) as exc_info:
            await exchange_nav_token(
                request=NavTokenExchangeRequest(session_id="  "),
                current_user={"user_id": "doctor_001"},
            )
        assert exc_info.value.status_code == 422

    def test_issued_token_validates_on_ws_path(self):
        """HTTP 交换签发的 token 必须能通过 WS 侧校验（同源 AuthTokenManager）。"""
        from app.api.websocket.nav import ConnectionManager

        async def _run():
            cm = ConnectionManager()
            t = await cm.auth_manager.generate_token("sess-ws")
            assert await cm.validate_auth_token("sess-ws", t) is True
            assert await cm.validate_auth_token("sess-ws", "wrong") is False

        asyncio.run(_run())


# ----------------------------------------------------------------------
# P0-3 consent 撤回单次投递
# ----------------------------------------------------------------------


class TestConsentSinglePublish:
    @pytest.mark.asyncio
    async def test_withdraw_does_not_double_publish(self):
        """API 层不再直接 xadd（唯一生产者 = ConsentRepository 内部投递）。"""
        from unittest.mock import patch

        from app.api.v1.consent import update_consent
        from app.models.schemas import ConsentAction, ConsentStatus, ConsentUpdateRequest

        mock_record = MagicMock()
        mock_record.patient_id = "P001"
        mock_record.status = ConsentStatus.WITHDRAWN.value

        with patch("app.api.v1.consent.ConsentRepository") as MockRepo, \
             patch("app.api.v1.consent.record_hsm_degradation_if_needed", new_callable=AsyncMock, return_value=False):
            MockRepo.return_value.create_consent = AsyncMock(return_value=mock_record)
            # 不抛异常即通过：API 层没有任何直接投递调用（源码级钉子另行断言）
            await update_consent(
                request=ConsentUpdateRequest(
                    patient_id="P001",
                    action=ConsentAction.WITHDRAW,
                    operator_id="admin",
                    consent_version="1.0",
                ),
                db=AsyncMock(),
                current_user={"user_id": "admin", "role": "SYSTEM_ADMIN"},
            )

    def test_source_has_no_direct_xadd(self):
        """源码级钉子：update_consent 不得再出现直接 xadd 调用。"""
        import inspect

        from app.api.v1 import consent as consent_api

        source = inspect.getsource(consent_api.update_consent)
        assert "xadd" not in source
        assert "RedisPool" not in source


# ----------------------------------------------------------------------
# P0-5 AlertEscalationManager 生命周期
# ----------------------------------------------------------------------


class TestAlertEscalationLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_loop_and_stop_cancels(self):
        from app.observability.alert_escalation.manager import AlertEscalationManager

        mgr = AlertEscalationManager()
        await mgr.start(check_interval=0.01)
        assert mgr._check_task is not None and not mgr._check_task.done()

        # 重复 start 幂等（不重建任务）
        first_task = mgr._check_task
        await mgr.start(check_interval=0.01)
        assert mgr._check_task is first_task

        await mgr.stop()
        assert mgr._check_task.done()


# ----------------------------------------------------------------------
# P0-4 设备 check-in / heartbeat / health / ota
# ----------------------------------------------------------------------


def _device_repo_mock(device_id="dev_edge_001", status="active", found=True):
    repo = AsyncMock()
    if found:
        device = MagicMock()
        device.device_id = device_id
        device.status = status
        repo.authenticate_device = AsyncMock(return_value=device)
    else:
        repo.authenticate_device = AsyncMock(return_value=None)
    return repo


class TestDeviceCheckInEndpoints:
    """POST /api/v1/admin/device/{checkin,heartbeat,health,ota}（设备头认证）。"""

    HEADERS = {"X-Device-Id": "dev_edge_001", "X-Device-Token": "ak_raw_key"}

    @pytest.fixture(autouse=True)
    def _setup(self):
        from fastapi.testclient import TestClient

        from app.dependencies import get_device_repo
        from app.main import app

        self._app = app
        self._client = TestClient(app)
        yield
        app.dependency_overrides.pop(get_device_repo, None)

    def test_checkin_success(self):
        from app.dependencies import get_device_repo

        self._app.dependency_overrides[get_device_repo] = lambda: _device_repo_mock()
        resp = self._client.post(
            "/api/v1/admin/device/checkin", json={"device_id": "dev_edge_001"},
            headers=self.HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["device_id"] == "dev_edge_001"
        assert data["registered"] is True

    def test_checkin_missing_headers_401(self):
        from app.dependencies import get_device_repo

        self._app.dependency_overrides[get_device_repo] = lambda: _device_repo_mock()
        resp = self._client.post(
            "/api/v1/admin/device/checkin", json={"device_id": "dev_edge_001"}
        )
        assert resp.status_code == 401

    def test_checkin_invalid_credentials_401(self):
        from app.dependencies import get_device_repo

        self._app.dependency_overrides[get_device_repo] = lambda: _device_repo_mock(found=False)
        resp = self._client.post(
            "/api/v1/admin/device/checkin", json={"device_id": "dev_edge_001"},
            headers=self.HEADERS,
        )
        assert resp.status_code == 401

    def test_heartbeat_success(self):
        from app.dependencies import get_device_repo

        self._app.dependency_overrides[get_device_repo] = lambda: _device_repo_mock()
        resp = self._client.post(
            "/api/v1/admin/device/heartbeat",
            json={"device_id": "dev_edge_001", "state": "online"},
            headers=self.HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_heartbeat_device_id_mismatch_400(self):
        from app.dependencies import get_device_repo

        self._app.dependency_overrides[get_device_repo] = lambda: _device_repo_mock()
        resp = self._client.post(
            "/api/v1/admin/device/heartbeat",
            json={"device_id": "dev_other", "state": "online"},
            headers=self.HEADERS,
        )
        assert resp.status_code == 400

    def test_health_success(self):
        from app.dependencies import get_device_repo

        self._app.dependency_overrides[get_device_repo] = lambda: _device_repo_mock()
        resp = self._client.post(
            "/api/v1/admin/device/health",
            json={"device_id": "dev_edge_001"},
            headers=self.HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ota_returns_501(self, monkeypatch):
        # 密封化（A.102）：部署机 .env 常配 OTA_PACKAGE_DIR，未钉住时会走
        # 真实分发路径返回 404 —— 本用例测的是"未配置 → 501"契约位。
        from app.config.settings import settings

        monkeypatch.setattr(settings, "ota_package_dir", None)
        from app.dependencies import get_device_repo

        self._app.dependency_overrides[get_device_repo] = lambda: _device_repo_mock()
        resp = self._client.get(
            "/api/v1/admin/device/ota/nav-model/1.2.0",
            headers=self.HEADERS,
        )
        assert resp.status_code == 501


# ----------------------------------------------------------------------
# P1-1 脱敏规则同步频道一致 + P1-8 阈值更新流生产者
# ----------------------------------------------------------------------


class TestDesensRulesSyncChannel:
    def test_sg_channel_matches_publisher(self):
        """SG 订阅频道必须与后端发布频道（DESSENS_RULES_UPDATE）一致。"""
        from app.config.redis_keys import RedisKey
        from safety_gateway.sg_config import sg_settings

        assert sg_settings.desens_rules_sync_channel == RedisKey.DESSENS_RULES_UPDATE

    def test_sg_main_uses_configured_channel(self):
        import inspect

        from safety_gateway import main as sg_main

        source = inspect.getsource(sg_main)
        assert '_RULES_SYNC_CHANNEL = settings.desens_rules_sync_channel' in source
        # 旧的裸名字面量不得残留
        assert '"desensitization_rules_update"' not in source


class TestSGThresholdProducer:
    def test_stream_constant_matches_consumer(self):
        from app.config.redis_keys import RedisKey
        from safety_gateway.threshold_subscriber import REDIS_STREAM_THRESHOLD_UPDATE

        assert RedisKey.SG_THRESHOLD_STREAM == REDIS_STREAM_THRESHOLD_UPDATE

    def test_legacy_dead_constants_removed(self):
        import app.config.redis_keys as rk

        assert not hasattr(rk.RedisKey, "SG_THRESHOLD_UPDATE")
        assert not hasattr(rk.LegacyRedisKey, "SG_THRESHOLD_UPDATE")

    @pytest.mark.asyncio
    async def test_producer_endpoint_xadds_to_stream(self):
        from unittest.mock import MagicMock, patch

        from app.api.v1.alert_admin import SGThresholdUpdateRequest, publish_sg_threshold_update
        from app.config.redis_keys import RedisKey

        mock_client = AsyncMock()
        with patch(
            "app.api.v1.alert_admin.RedisPool.get_client", AsyncMock(return_value=mock_client)
        ):
            resp = await publish_sg_threshold_update(
                request=SGThresholdUpdateRequest(
                    action="update", organ="thyroid", threshold=0.7
                ),
                current_user={"user_id": "admin", "role": "SYSTEM_ADMIN"},
            )
        mock_client.xadd.assert_awaited_once()
        args = mock_client.xadd.await_args
        assert args.args[0] == RedisKey.SG_THRESHOLD_STREAM
        assert resp["status"] == "published"

    @pytest.mark.asyncio
    async def test_producer_update_requires_organ_and_threshold(self):
        import fastapi

        from app.api.v1.alert_admin import SGThresholdUpdateRequest, publish_sg_threshold_update

        with pytest.raises(fastapi.HTTPException) as exc_info:
            await publish_sg_threshold_update(
                request=SGThresholdUpdateRequest(action="update", organ="thyroid"),
                current_user={"user_id": "admin"},
            )
        assert exc_info.value.status_code == 422
