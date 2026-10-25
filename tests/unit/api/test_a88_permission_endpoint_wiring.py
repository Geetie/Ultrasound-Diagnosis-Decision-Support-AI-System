"""A.88 配置接线②（接线侧）：6 类关键端点必须真的挂上 ``check_permission``。

`tests/unit/sec/test_a88_permission_wiring.py` 钉的是**准入器本身**的语义；
本文件钉的是"**有没有接上**"——这是本次修复的核心缺陷形态：权限位表存在，
但零生产调用。因此除了行为测试，还必须有源码/依赖级守卫，否则下一次重构
把 ``Depends(PermissionChecker(...))`` 摘掉时，行为测试可能因为"测试替身
直接绕过依赖"而全绿。

四类断言：
1. 路由依赖里真的列出了对应权限（用 FastAPI 的 ``dependant`` 而不是正则）；
2. 有权限 → 业务 200；无权限 → 403 + 既有 E1008 错误码 + 既有 detail 文案；
3. fail-closed：不带 role 的已登录用户 → 403（不放行）；
4. 反证腿：``PermissionChecker`` 若被改成"只要求登录"，第 2、3 条即红。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.security.auth import PermissionChecker

# 端点 (path, method) → 期望的权限位
EXPECTED_ENDPOINTS = [
    ("/api/v1/diagnosis/request", "post", "submit_diagnosis"),
    ("/api/v1/diagnosis/multi-organ", "post", "submit_diagnosis"),
    ("/api/v1/diagnosis/result/{request_id}", "get", "view_results"),
    ("/api/v1/report/{report_id}/sign", "post", "sign_report"),
    ("/api/v1/feedback", "post", "submit_feedback"),
    ("/api/v1/internal/obs/dashboard", "get", "view_qc_report"),
    ("/api/v1/admin/alert/sg-threshold", "post", "configure_sg_thresholds"),
]


def _permission_checkers(dependant) -> list[PermissionChecker]:
    found: list[PermissionChecker] = []
    for sub in dependant.dependencies:
        call = sub.call
        if isinstance(call, PermissionChecker):
            found.append(call)
        found.extend(_permission_checkers(sub))
    return found


@pytest.fixture(scope="module")
def app_routes():
    from app.main import app

    table: dict[tuple[str, str], list[PermissionChecker]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        dependant = getattr(route, "dependant", None)
        if not path or dependant is None:
            continue
        for method in methods:
            if method.lower() in ("head", "options"):
                continue
            table[(path, method.lower())] = _permission_checkers(dependant)
    return table


class TestEndpointsCarryPermissionDependency:
    @pytest.mark.parametrize("path,method,permission", EXPECTED_ENDPOINTS)
    def test_permission_dependency_present(self, app_routes, path, method, permission):
        """反证腿：把这处 ``Depends(PermissionChecker(...))`` 删掉，本用例即红。"""
        checkers = app_routes.get((path, method))
        assert checkers is not None, f"路由不存在：{method.upper()} {path}"
        permissions = [c.permission for c in checkers]
        assert permission in permissions, (
            f"{method.upper()} {path} 未挂 {permission} 权限依赖，实际={permissions}"
        )

    def test_single_organ_diagnosis_does_not_gain_extra_checkers(self, app_routes):
        """单器官端点只应拿到一个权限位（不许多挂，避免语义漂移）。"""
        checkers = app_routes[("/api/v1/diagnosis/request", "post")]
        assert [c.permission for c in checkers] == ["submit_diagnosis"]


class TestDenialIsObservableAsBefore:
    """403 的可观测契约不变：status 403 + detail 文案 + （经错误处理器）E1008。"""

    def test_default_detail_matches_role_checker(self):
        from app.security.auth import RoleChecker

        with pytest.raises(HTTPException) as perm_exc:
            PermissionChecker("sign_report")({"user_id": "u", "role": "EMERGENCY_DOCTOR"})
        with pytest.raises(HTTPException) as role_exc:
            RoleChecker(["ULTRASOUND_DOCTOR"])({"user_id": "u", "role": "EMERGENCY_DOCTOR"})
        assert perm_exc.value.status_code == role_exc.value.status_code == 403
        assert perm_exc.value.detail == role_exc.value.detail

    def test_denial_logged_with_permission_and_role(self):
        """拒绝必须留痕（否则运维只能看到一堆 403，不知道为什么）。"""
        import app.security.auth as auth_mod

        calls = []
        original_warning = auth_mod.logger.warning

        def _spy(event, **kwargs):
            calls.append((event, kwargs))
            return original_warning(event, **kwargs)

        auth_mod.logger.warning = _spy
        try:
            with pytest.raises(HTTPException):
                PermissionChecker("sign_report")({"user_id": "u1", "role": "QUALITY_CONTROL"})
        finally:
            auth_mod.logger.warning = original_warning

        assert any(
            event == "permission_denied"
            and payload.get("permission") == "sign_report"
            and payload.get("role") == "QUALITY_CONTROL"
            for event, payload in calls
        ), calls


class TestHttpBehaviour:
    """真 HTTP 层：有权限 200 / 无权限 403（用最省的端点避免外部依赖）。"""

    def test_sg_threshold_denied_for_doctor(self, test_client):
        """/alerts/sg-threshold 只给管理员（权限位 configure_sg_thresholds）。"""
        from app.main import app
        from app.security.auth import get_current_active_user

        app.dependency_overrides[get_current_active_user] = lambda: {
            "user_id": "doc_1",
            "role": "ULTRASOUND_DOCTOR",
        }
        try:
            resp = test_client.post(
                "/api/v1/admin/alert/sg-threshold",
                json={"action": "refresh"},
            )
        finally:
            app.dependency_overrides.pop(get_current_active_user, None)

        assert resp.status_code == 403
        assert resp.json()["error_code"] == "E1008"

    def test_sg_threshold_allows_admin_through_permission_gate(self, test_client):
        """管理员能过权限门（后面的 Redis 逻辑用桩替身，不验证副作用）。"""
        from unittest.mock import AsyncMock, patch

        from app.main import app
        from app.security.auth import get_current_active_user

        app.dependency_overrides[get_current_active_user] = lambda: {
            "user_id": "admin_1",
            "role": "SYSTEM_ADMIN",
        }
        mock_client = AsyncMock()
        try:
            with patch(
                "app.api.v1.alert_admin.RedisPool.get_client",
                AsyncMock(return_value=mock_client),
            ):
                resp = test_client.post(
                    "/api/v1/admin/alert/sg-threshold",
                    json={"action": "update", "organ": "thyroid", "threshold": 0.7},
                )
        finally:
            app.dependency_overrides.pop(get_current_active_user, None)

        assert resp.status_code == 200, resp.text

    def test_sg_threshold_fails_closed_without_role(self, test_client):
        """反证腿：无 role 若被放行，这条即红（fail-closed 是本次的核心口径）。"""
        from app.main import app
        from app.security.auth import get_current_active_user

        app.dependency_overrides[get_current_active_user] = lambda: {"user_id": "anon-ish"}
        try:
            resp = test_client.post(
                "/api/v1/admin/alert/sg-threshold",
                json={"action": "refresh"},
            )
        finally:
            app.dependency_overrides.pop(get_current_active_user, None)

        assert resp.status_code == 403
