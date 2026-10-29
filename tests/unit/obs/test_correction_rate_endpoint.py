"""05-3 S0 守卫：改判率只读端点必须鉴权、必须带"≠漏诊率"免责、必须只读。

反证腿：
- 若有人删掉 router 级鉴权 → 匿名 200 用例红；
- 若有人把 disclaimer/metric_kind 删掉 → 文案用例红（防"改判率冒充漏诊率"）；
- 端点不得产生写路径（只 select feedback_records）。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.obs_dashboard import router as obs_dashboard_router
from app.security.auth import get_current_active_user


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(obs_dashboard_router, prefix="/api/v1/internal/obs")
    return TestClient(app, raise_server_exceptions=False)


def test_anonymous_request_rejected(client):
    resp = client.get("/api/v1/internal/obs/correction-rate")
    assert resp.status_code in (401, 403, 422)
    assert resp.status_code != 200


def test_unauthorized_role_rejected_403(client):
    doctor = {"role": "ULTRASOUND_DOCTOR", "user_id": "d1"}

    def _fake_user():
        return doctor

    client.app.dependency_overrides[get_current_active_user] = _fake_user
    try:
        resp = client.get("/api/v1/internal/obs/correction-rate")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.pop(get_current_active_user, None)


def test_response_carries_disclaimer_and_proxy_marker(client, monkeypatch):
    """QC 权限角色可读；响应必须含 metric_kind=proxy 与 disclaimer 原文关键词。"""
    qc = {"role": "QUALITY_CONTROL", "user_id": "qc1"}

    def _fake_user():
        return qc

    async def _fake_assess(session, *, window_days=None):
        from app.services.correction_rate_monitor import CorrectionRateReport

        return CorrectionRateReport(considered=25, corrected=5)

    monkeypatch.setattr(
        "app.api.v1.obs_dashboard.assess_recent_corrections", _fake_assess
    )
    client.app.dependency_overrides[get_current_active_user] = _fake_user
    try:
        resp = client.get("/api/v1/internal/obs/correction-rate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["metric_kind"] == "proxy_correction_rate"
        assert "≠漏诊率" in body["disclaimer"]
        assert body["insufficient_samples"] is False
        assert body["report"]["considered"] == 25
        assert body["report"]["correction_rate"] == 0.2
    finally:
        client.app.dependency_overrides.pop(get_current_active_user, None)


def test_insufficient_samples_flagged_not_alerted(client, monkeypatch):
    """低于 min_samples 只统计不判定（monitor :229-231），端点必须透传该标记。"""
    qc = {"role": "QUALITY_CONTROL", "user_id": "qc1"}

    def _fake_user():
        return qc

    async def _fake_assess(session, *, window_days=None):
        from app.services.correction_rate_monitor import CorrectionRateReport

        return CorrectionRateReport(considered=3, corrected=1, insufficient_samples=True)

    monkeypatch.setattr(
        "app.api.v1.obs_dashboard.assess_recent_corrections", _fake_assess
    )
    client.app.dependency_overrides[get_current_active_user] = _fake_user
    try:
        body = client.get("/api/v1/internal/obs/correction-rate").json()
        assert body["insufficient_samples"] is True
    finally:
        client.app.dependency_overrides.pop(get_current_active_user, None)
