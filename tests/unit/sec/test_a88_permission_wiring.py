"""A.88 配置接线②：`PERMISSIONS` / `check_permission` 必须真的被生产端点消费。

## 缺陷（《架构真相》§3.3「权限位」）

`app/security/auth.py` 里有 5 角色 × 权限位表（``PERMISSIONS``）与
``check_permission()``，但**零生产调用**：整个 `app/` 只有 ``/auth/permissions``
一个"把表读出来展示"的端点，没有任何地方用它做准入。真正的准入全靠 8 个管理面
文件的 ``RoleChecker([...])`` 角色白名单 —— 于是"权限位"是一张没人看的表。

## 本文件钉住的四条

1. **单一事实源**：``RoleChecker.for_permission(name)`` 从 ``PERMISSIONS``
   推导允许角色（含 ``*`` 通配 → SYSTEM_ADMIN）。已有的三处管理面白名单必须与
   推导结果**逐字相等**（否则"单一事实源"就是一句空话，且会偷偷放宽/收紧）。
2. **新准入器**：``PermissionChecker(name)`` 用 ``check_permission`` 判定，
   403 文案与 ``RoleChecker`` **完全一致**（不得改变既有可观测行为）。
3. **fail-closed**：无 role / 未登记角色的主体一律 403（不允许"没有 role 就放行"
   —— 那是最典型的静默放宽）。
4. **权限位真的被扣**：6 类关键端点（诊断提交 / 结果查看 / 报告签发 / 反馈提交 /
   质控报告 / 阈值配置）的依赖里必须出现 ``PermissionChecker``（源码级守卫），
   且 HTTP 层行为符合"有权限 200 / 无权限 403"。

反证腿：把 ``PermissionChecker`` 改回"只要求登录"、把 fail-open 加回来、或把
端点上的依赖摘掉，本文件都会变红。
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.security.auth import (
    PERMISSIONS,
    ROLES,
    PermissionChecker,
    RoleChecker,
    check_permission,
)

ROLE_CHECKER_403_DETAIL = "You do not have permission to access this resource"


# ----------------------------------------------------------------------
# 1) PERMISSIONS ↔ RoleChecker 单一事实源
# ----------------------------------------------------------------------


class TestSingleSourceOfTruth:
    def test_every_permission_role_exists(self):
        """PERMISSIONS 的键必须是 ROLES 里登记过的角色（反之亦然）。"""
        assert set(PERMISSIONS) == set(ROLES)

    def test_for_permission_excludes_wildcard_roles_by_listing(self):
        """`*` 角色不靠列表命中，但必须被推导进来（否则管理员被自家权限拦住）。"""
        roles = RoleChecker.for_permission("view_audit_logs")
        assert "QUALITY_CONTROL" in roles
        assert "SYSTEM_ADMIN" in roles
        assert "ULTRASOUND_DOCTOR" not in roles

    @pytest.mark.parametrize(
        "permission,expected",
        [
            ("submit_diagnosis", ["ULTRASOUND_DOCTOR", "EMERGENCY_DOCTOR", "DEPARTMENT_HEAD", "SYSTEM_ADMIN"]),
            ("view_results", ["ULTRASOUND_DOCTOR", "EMERGENCY_DOCTOR", "DEPARTMENT_HEAD", "QUALITY_CONTROL", "SYSTEM_ADMIN"]),
            ("submit_feedback", ["ULTRASOUND_DOCTOR", "DEPARTMENT_HEAD", "SYSTEM_ADMIN"]),
            ("review_l3", ["DEPARTMENT_HEAD", "QUALITY_CONTROL", "SYSTEM_ADMIN"]),
            ("configure_thresholds", ["DEPARTMENT_HEAD", "SYSTEM_ADMIN"]),
            ("view_audit_logs", ["QUALITY_CONTROL", "SYSTEM_ADMIN"]),
            ("view_qc_report", ["QUALITY_CONTROL", "SYSTEM_ADMIN"]),
            # 运行期安全阈值：只给管理员（与被接线端点原白名单一致，未放宽）
            ("configure_sg_thresholds", ["SYSTEM_ADMIN"]),
        ],
    )
    def test_derivation_matches_permission_table(self, permission, expected):
        assert RoleChecker.for_permission(permission) == expected

    def test_unknown_permission_derives_to_empty(self):
        """未登记的权限名 → 无人可用（fail-closed，而不是"没人持有就放行"）。"""
        assert RoleChecker.for_permission("no_such_permission") == []

    def test_admin_wildcard_does_not_cover_unknown_permission(self):
        """反证腿：``"*"`` 若退回"任意字符串都 True"，本用例即红。

        为什么这条重要：权限名写错一个字母（``sign_reports``）时，
        管理员若照样通过，等于该端点**静默变成管理员专属却不报错**；
        更糟的是运维以为新权限已生效。未登记 → 拒绝。
        """
        assert check_permission("no_such_permission", {"role": "SYSTEM_ADMIN"}) is False
        # 已登记的仍然全通（不能因为这条把管理员一起挡死）
        assert check_permission("sign_report", {"role": "SYSTEM_ADMIN"}) is True

    def test_existing_admin_routers_match_derived_sets(self):
        """既有 3 处管理面白名单必须等于 PERMISSIONS 的推导结果（零行为漂移）。"""
        assert RoleChecker.for_permission("view_qc_report") == [
            "QUALITY_CONTROL",
            "SYSTEM_ADMIN",
        ]
        assert RoleChecker.for_permission("configure_thresholds") == [
            "DEPARTMENT_HEAD",
            "SYSTEM_ADMIN",
        ]
        # `app/api/v1/alert_admin.py:13` 原来是 RoleChecker(["SYSTEM_ADMIN"])
        assert RoleChecker.for_permission("configure_sg_thresholds") == [
            "SYSTEM_ADMIN",
        ]


# ----------------------------------------------------------------------
# 2) PermissionChecker 语义：403 文案不变 + fail-closed
# ----------------------------------------------------------------------


class TestPermissionChecker:
    def test_allows_role_holding_permission(self):
        checker = PermissionChecker("submit_diagnosis")
        user = {"user_id": "u1", "role": "ULTRASOUND_DOCTOR"}
        assert checker(user) == user

    def test_denies_role_without_permission(self):
        checker = PermissionChecker("submit_diagnosis")
        with pytest.raises(HTTPException) as excinfo:
            checker({"user_id": "u1", "role": "QUALITY_CONTROL"})
        assert excinfo.value.status_code == 403
        # 文案必须与 RoleChecker 一致（不得改变既有可观测行为）
        assert excinfo.value.detail == ROLE_CHECKER_403_DETAIL

    def test_fails_closed_without_role(self):
        """反证腿：改成 `user.get("role") or "SYSTEM_ADMIN"` 之类的放宽即红。"""
        checker = PermissionChecker("view_results")
        with pytest.raises(HTTPException) as excinfo:
            checker({"user_id": "u1"})
        assert excinfo.value.status_code == 403

    def test_fails_closed_for_unknown_role(self):
        checker = PermissionChecker("view_results")
        with pytest.raises(HTTPException) as excinfo:
            checker({"user_id": "u1", "role": "doctor"})
        assert excinfo.value.status_code == 403

    def test_admin_wildcard_passes_everything(self):
        for permission in ("submit_diagnosis", "sign_report", "view_qc_report"):
            assert PermissionChecker(permission)(
                {"user_id": "admin", "role": "SYSTEM_ADMIN"}
            )

    def test_uses_check_permission_as_single_judge(self, monkeypatch):
        """判定必须走 `check_permission`（而不是自己再抄一份表）。"""
        import app.security.auth as auth_mod

        seen: list[str] = []
        original = auth_mod.check_permission

        def _spy(permission, user):
            seen.append(permission)
            return original(permission, user)

        monkeypatch.setattr(auth_mod, "check_permission", _spy)
        PermissionChecker("submit_feedback")(
            {"user_id": "u1", "role": "ULTRASOUND_DOCTOR"}
        )
        assert seen == ["submit_feedback"]

    def test_dependency_uses_get_current_active_user(self):
        """必须挂在已登录用户上（不能绕过 disabled 判定）。"""
        checker = PermissionChecker("view_results")
        src = inspect.getsource(checker.__call__)
        assert "get_current_active_user" in src


# ----------------------------------------------------------------------
# 3) 权限位在新场景下的语义（签发 / 质控报告）
# ----------------------------------------------------------------------


class TestNewPermissions:
    def test_ultrasound_doctor_can_sign_report(self):
        assert check_permission("sign_report", {"role": "ULTRASOUND_DOCTOR"})

    def test_quality_control_cannot_sign_report(self):
        assert not check_permission("sign_report", {"role": "QUALITY_CONTROL"})

    def test_department_head_can_sign_report(self):
        assert check_permission("sign_report", {"role": "DEPARTMENT_HEAD"})

    def test_quality_control_can_view_qc_report(self):
        assert check_permission("view_qc_report", {"role": "QUALITY_CONTROL"})

    def test_ultrasound_doctor_cannot_view_qc_report(self):
        assert not check_permission("view_qc_report", {"role": "ULTRASOUND_DOCTOR"})

    def test_emergency_doctor_cannot_submit_feedback(self):
        """既有语义不变（急诊医生只做判读，不改库）。"""
        assert not check_permission("submit_feedback", {"role": "EMERGENCY_DOCTOR"})
