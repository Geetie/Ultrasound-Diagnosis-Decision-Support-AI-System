"""P0-5 守卫：/api/v1/internal/obs/* 四端点必须角色认证（无匿名访问）。

背景（15 号计划 P0-5）：这些端点暴露指标仪表盘、冷归档状态，且
POST /cold-archive/run 可触发归档运行——此前完全无认证。
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


def test_匿名请求被拒_401(client):
    # 无 token → oauth2_scheme 422/401；核心断言：绝不 200
    resp = client.get("/api/v1/internal/obs/dashboard")
    assert resp.status_code in (401, 403, 422)


def test_非授权角色被拒_403(client):

    doctor = {"role": "ULTRASOUND_DOCTOR", "user_id": "d1"}

    def _fake_user():
        return doctor

    client.app.dependency_overrides[get_current_active_user] = _fake_user
    try:
        resp = client.get("/api/v1/internal/obs/dashboard")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.pop(get_current_active_user, None)


@pytest.mark.parametrize(
    "path, method",
    [
        ("/api/v1/internal/obs/dashboard", "get"),
        ("/api/v1/internal/obs/cold-archive", "get"),
        ("/api/v1/internal/obs/restore-rate", "get"),
        ("/api/v1/internal/obs/cold-archive/run", "post"),
    ],
)
def test_四端点全部要求认证(client, path, method):
    """即使角色正确，未带凭据也绝不允许匿名 200（守卫：接线不可回退）。"""
    resp = client.get(path) if method == "get" else client.post(path)
    assert resp.status_code != 200, f"{path} 存在匿名访问！"
