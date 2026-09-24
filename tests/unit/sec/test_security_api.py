
import pytest
from fastapi import HTTPException

from app.security.auth import RoleChecker

pytestmark = [pytest.mark.track_sec, pytest.mark.track_api, pytest.mark.sec_domain_auth]


class TestSecurityAPIAuth:
    """验证Security API端点需要认证和授权"""

    def test_role_checker_returns_user_dict(self):
        """RoleChecker返回user对象"""
        checker = RoleChecker(["SYSTEM_ADMIN"])
        user = {"user_id": "admin", "role": "SYSTEM_ADMIN", "username": "admin"}
        result = checker(user)
        assert result == user
        assert isinstance(result, dict)

    def test_role_checker_rejects_unauthorized_role(self):
        """RoleChecker拒绝未授权角色"""
        checker = RoleChecker(["SYSTEM_ADMIN"])
        user = {"user_id": "doctor", "role": "ULTRASOUND_DOCTOR"}
        with pytest.raises(HTTPException) as exc_info:
            checker(user)
        assert exc_info.value.status_code == 403

    def test_role_checker_rejects_no_role(self):
        """RoleChecker拒绝无角色用户"""
        checker = RoleChecker(["SYSTEM_ADMIN"])
        user = {"user_id": "unknown"}
        with pytest.raises(HTTPException) as exc_info:
            checker(user)
        assert exc_info.value.status_code == 403

    def test_admin_only_roles(self):
        """SYSTEM_ADMIN角色通过管理员检查"""
        checker = RoleChecker(["SYSTEM_ADMIN"])
        admin_user = {"user_id": "admin", "role": "SYSTEM_ADMIN"}
        assert checker(admin_user) == admin_user

    def test_admin_or_head_roles(self):
        """SYSTEM_ADMIN和DEPARTMENT_HEAD通过检查"""
        checker = RoleChecker(["SYSTEM_ADMIN", "DEPARTMENT_HEAD"])
        admin_user = {"user_id": "admin", "role": "SYSTEM_ADMIN"}
        head_user = {"user_id": "head", "role": "DEPARTMENT_HEAD"}
        assert checker(admin_user) == admin_user
        assert checker(head_user) == head_user

    def test_desensitization_test_no_original_field(self):
        """脱敏测试端点不返回original字段"""
        from app.security.desensitization import desensitization_engine

        test_text = "患者张三的身份证号110101199001011234"
        result = desensitization_engine.desensitize(test_text)
        assert "张三" not in result
        assert "110101199001011234" not in result

    def test_security_api_all_endpoints_require_auth(self):
        """验证所有Security API端点都需要认证"""
        from app.api.v1.security import router

        for route in router.routes:
            if hasattr(route, 'dependant') and route.dependant:
                has_auth = any(
                    (hasattr(dep, 'call') and dep.call and
                     (getattr(dep.call, '__name__', None) in ('get_current_active_user', 'RoleChecker')
                      or dep.call.__class__.__name__ == 'RoleChecker'))
                    for dep in route.dependant.dependencies
                )
                assert has_auth, f"Endpoint {route.path} lacks authentication"
