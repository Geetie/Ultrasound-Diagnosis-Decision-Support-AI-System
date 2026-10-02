"""内部告警回调端点测试（`/api/v1/internal/alerts/webhook`）。

## 为什么有这条测试

`config/alertmanager.yml:59` 与 `docker-compose.dev.yml:247` 都把回调指向这个路径，
但**代码里从来没有这个路由** → Alertmanager 的 webhook receiver 与 vmbackup 失败通知
一直在收 404。本测试既锁端点行为，也断言**路由确实被注册**（否则又是"实现了没接线"）。
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.alerts import router as alerts_router
from app.config.settings import settings

PATH = "/api/v1/internal/alerts/webhook"

ALERTMANAGER_PAYLOAD = {
    "version": "4",
    "status": "firing",
    "groupKey": '{}:{alertname="HallucinationRateHigh"}',
    "receiver": "webhook",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "HallucinationRateHigh", "severity": "critical"},
            "annotations": {"summary": "幻觉率超阈值"},
            "startsAt": "2026-09-28T10:00:00Z",
            "fingerprint": "abc123",
        },
        {
            "status": "resolved",
            "labels": {"alertname": "GpuMemoryHigh", "severity": "warning"},
            "fingerprint": "def456",
        },
    ],
}

VMBACKUP_PAYLOAD = {
    "type": "vmbackup_failure",
    "message": "VictoriaMetrics hourly backup failed",
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(alerts_router, prefix="/api/v1/internal/alerts")
    return TestClient(app)


class TestAlertmanagerPayload:
    def test_counts_firing_and_resolved(self, client):
        resp = client.post(PATH, json=ALERTMANAGER_PAYLOAD)

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "alertmanager"
        assert body["firing"] == 1
        assert body["resolved"] == 1
        assert body["alert_names"] == ["GpuMemoryHigh", "HallucinationRateHigh"]

    def test_empty_alerts_is_accepted(self, client):
        """空告警载荷也要 200：否则 Alertmanager 会进入重试退避。"""
        resp = client.post(PATH, json={"version": "4", "status": "resolved", "alerts": []})

        assert resp.status_code == 200
        assert resp.json()["firing"] == 0


class TestVmbackupPayload:
    def test_recognises_vmbackup_source(self, client):
        resp = client.post(PATH, json=VMBACKUP_PAYLOAD)

        assert resp.status_code == 200
        assert resp.json()["source"] == "vmbackup"

    def test_unknown_payload_still_accepted(self, client):
        """未知形状也返回 200 + source=unknown：接收端不做拒绝，避免发送方重试风暴。"""
        resp = client.post(PATH, json={"anything": 1})

        assert resp.status_code == 200
        assert resp.json()["source"] == "unknown"


class TestTokenEnforcement:
    def test_accepts_when_token_not_configured(self, client):
        """未配置令牌 = 内网部署语义，放行。"""
        with patch.object(settings, "alert_webhook_token", "", create=True):
            assert client.post(PATH, json=VMBACKUP_PAYLOAD).status_code == 200

    def test_rejects_when_token_configured_but_missing(self, client):
        """**配置了就必须校验**（fail-closed）：避免"以为有鉴权实际没有"。"""
        with patch.object(settings, "alert_webhook_token", "s3cr3t", create=True):
            resp = client.post(PATH, json=VMBACKUP_PAYLOAD)

        assert resp.status_code == 401

    def test_rejects_wrong_token(self, client):
        with patch.object(settings, "alert_webhook_token", "s3cr3t", create=True):
            resp = client.post(
                PATH, json=VMBACKUP_PAYLOAD, headers={"X-Alert-Token": "wrong"}
            )

        assert resp.status_code == 401

    def test_accepts_header_token(self, client):
        with patch.object(settings, "alert_webhook_token", "s3cr3t", create=True):
            resp = client.post(
                PATH, json=ALERTMANAGER_PAYLOAD, headers={"X-Alert-Token": "s3cr3t"}
            )

        assert resp.status_code == 200

    def test_accepts_bearer_token(self, client):
        """Alertmanager 的 http_config.authorization 走 Bearer 头。"""
        with patch.object(settings, "alert_webhook_token", "s3cr3t", create=True):
            resp = client.post(
                PATH,
                json=ALERTMANAGER_PAYLOAD,
                headers={"Authorization": "Bearer s3cr3t"},
            )

        assert resp.status_code == 200


class TestRouteIsRegisteredInApp:
    """配置指向的路径必须真实存在——这是本次缺陷的直接对手。"""

    def test_app_registers_the_configured_path(self):
        import app.main as main_module

        paths = {route.path for route in main_module.app.routes if hasattr(route, "path")}

        assert "/api/v1/internal/alerts/webhook" in paths
