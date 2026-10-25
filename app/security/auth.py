import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
import structlog
from cryptography.exceptions import InvalidKey
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.config.settings import settings
from app.security.hsm import KeySlot, hsm_manager

logger = structlog.get_logger()

JWT_KEY_NAME = "jwt_signing_key"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode('utf-8'),
        hashed_password.encode('utf-8')
    )

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

oauth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(tokenUrl="api/v1/auth/token")

def _ensure_jwt_key_exists() -> None:
    keys = hsm_manager.list_keys()
    if JWT_KEY_NAME not in keys:
        logger.info("jwt_key_not_found_creating_new", key_name=JWT_KEY_NAME)
        hsm_manager.generate_key_pair(JWT_KEY_NAME)

def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    _ensure_jwt_key_exists()

    to_encode: dict[str, Any] = data.copy()
    if expires_delta:
        expire: datetime = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)

    now = datetime.now(timezone.utc)
    to_encode.update({
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
        "jti": secrets.token_urlsafe(16)
    })

    header = {"alg": settings.jwt_algorithm, "typ": "JWT", "kid": JWT_KEY_NAME}
    try:
        encoded_jwt: str = hsm_manager.sign_jwt(JWT_KEY_NAME, header, to_encode, settings.jwt_algorithm)
        return encoded_jwt
    except (InvalidKey, KeyError, ConnectionError, OSError) as e:
        hsm_manager.set_degraded(f"jwt_signing_failed: {e}")
        raise
    except Exception as e:
        logger.error("jwt_signing_error", error=str(e))
        raise

def decode_access_token(token: str) -> dict[str, Any]:
    public_key = hsm_manager.get_public_key(JWT_KEY_NAME)
    if public_key is None:
        hsm_manager.set_degraded("jwt_public_key_unavailable")
        raise ValueError("JWT public key not available")

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_exp": True}
        )
        return payload
    except jwt.InvalidTokenError:  # silent-ok: 逐个密钥试签，全部失败后统一 warning（循环外留痕）
        pass

    all_keys = hsm_manager.list_keys_by_slot(KeySlot.JWT_SIGNING)
    for key_name in all_keys:
        if key_name == JWT_KEY_NAME or not key_name.startswith(f"{JWT_KEY_NAME}_v"):
            continue
        old_public_key = hsm_manager.get_public_key(key_name)
        if old_public_key is None:
            continue
        try:
            payload = jwt.decode(
                token,
                old_public_key,
                algorithms=["RS256"],
                options={"verify_exp": True}
            )
            logger.info("jwt_verified_with_rotated_key", key_name=key_name)
            return payload
        except jwt.InvalidTokenError:  # silent-ok: 逐个密钥试签，全部失败后统一 warning（循环外留痕）
            continue

    logger.warning("jwt_decode_failed_all_keys_tried")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    payload: dict[str, Any] = decode_access_token(token)
    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_info: dict[str, Any] = payload.get("user_info", {}).copy()
    user_info["user_id"] = user_id

    return user_info

def get_current_active_user(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if current_user.get("disabled", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return current_user

class RoleChecker:
    def __init__(self, allowed_roles: list[str]) -> None:
        self.allowed_roles: list[str] = allowed_roles

    @staticmethod
    def for_permission(permission: str) -> list[str]:
        """从 :data:`PERMISSIONS` **推导**持有该权限位的角色列表（单一事实源）。

        A.88 配置接线②：此前 ``PERMISSIONS`` 与 8 处 ``RoleChecker([...])``
        白名单是**两份互不相干的事实**——前者零调用、后者硬编码角色名，
        于是"权限位表"既不能作为准入依据，也难以核对谁真的能做什么。

        推导规则（与 :func:`check_permission` 完全同构，含 ``*`` 通配）：
        ``"*" in PERMISSIONS[role]`` 或 ``permission in PERMISSIONS[role]``。

        顺序按 :data:`ROLES` 的声明顺序（稳定、可断言），未登记者不出现。

        Args:
            permission: 权限位名（如 ``"submit_diagnosis"``）。

        Returns:
            持有该权限的角色名列表；**未登记的权限名返回空列表**
            （fail-closed：没人可用，而不是"没人持有就放行"）。
        """
        if permission not in KNOWN_PERMISSIONS:
            return []
        holders: list[str] = []
        for role in ROLES:
            granted = PERMISSIONS.get(role, [])
            if "*" in granted or permission in granted:
                holders.append(role)
        return holders

    def __call__(self, user: dict[str, Any] = Depends(get_current_active_user)) -> dict[str, Any]:
        user_role: str | None = user.get("role")
        if user_role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource"
            )
        return user

ROLES: dict[str, str] = {
    "ULTRASOUND_DOCTOR": "超声科医生",
    "EMERGENCY_DOCTOR": "急诊科医生",
    "DEPARTMENT_HEAD": "科室主任",
    "QUALITY_CONTROL": "质控管理员",
    "SYSTEM_ADMIN": "系统管理员"
}

PERMISSIONS: dict[str, list[str]] = {
    "ULTRASOUND_DOCTOR": [
        "submit_diagnosis",
        "view_results",
        "submit_feedback",
        # A.88：报告签发 = 医生对结论背书（原来只有"提交/查看/反馈"三个权限位，
        # 签发这个最需要背书动作的入口反而没有任何权限位覆盖）。
        "sign_report",
    ],
    "EMERGENCY_DOCTOR": ["submit_diagnosis", "view_results"],
    "DEPARTMENT_HEAD": [
        "submit_diagnosis",
        "view_results",
        "submit_feedback",
        "review_l3",
        "configure_thresholds",
        # 科室主任可代签（科室管理职责），与既有 `RoleChecker(["ULTRASOUND_DOCTOR",
        # "DEPARTMENT_HEAD"])` 的口径保持一致。
        "sign_report",
    ],
    "QUALITY_CONTROL": [
        "view_results",
        "review_l3",
        "view_audit_logs",
        # A.88：质控报告查看 —— 取值**严格等于**既有管理面白名单
        # `app/api/v1/obs_dashboard.py:19` 的 `RoleChecker(["SYSTEM_ADMIN",
        # "QUALITY_CONTROL"])`，接线后零行为漂移（不趁机放宽）。
        "view_qc_report",
    ],
    "SYSTEM_ADMIN": ["*"]
}

#: A.88：``"*"`` 通配的含义是"**所有已登记的权限位**"，而不是"任意字符串"。
#: 因此推导 :meth:`RoleChecker.for_permission` 时先看这个集合：
#: 未登记的权限名一律返回空列表（fail-closed）——否则管理员会因为 ``"*"``
#: 对任何拼错的权限名都返回 True，"权限位"就又退化成一张没人核对的表。
#:
#: 为什么还要单独列 :data:`ADMIN_PERMISSIONS`：``SYSTEM_ADMIN`` 用的是 ``"*"``，
#: 它无法"显式登记"一个只属于自己的权限名。运行期安全阈值（``sg:threshold:stream``）
#: 属于这类：发布它会即时改变对患者的放行/拦截行为，权限只给系统管理员，
#: 与被接线端点原来的白名单（仅 ``SYSTEM_ADMIN``）**完全一致**。
ADMIN_PERMISSIONS: frozenset[str] = frozenset({
    "configure_sg_thresholds",
})

KNOWN_PERMISSIONS: frozenset[str] = frozenset(
    {permission for granted in PERMISSIONS.values() for permission in granted if permission != "*"}
    | ADMIN_PERMISSIONS
)

def check_permission(permission: str, user: dict[str, Any]) -> bool:
    """该用户是否持有 ``permission`` 权限位（**fail-closed**）。

    A.88：``"*"`` 通配现在只覆盖 :data:`KNOWN_PERMISSIONS`（已登记的权限位），
    不再对任意字符串返回 True。理由：``"*"`` 若对拼错的权限名也放行，
    "权限位"就无法充当单一事实源——写错一个字母的端点会**静默变成人人可用**，
    而这正是权限表最容易腐坏的方式。未登记 → 一律 False（拒绝），
    新权限必须先登记进 PERMISSIONS 才能被授予。
    """
    user_role: str | None = user.get("role")
    if user_role is None:
        return False

    if permission not in KNOWN_PERMISSIONS:
        return False

    role_permissions: list[str] = PERMISSIONS.get(user_role, [])
    if "*" in role_permissions:
        return True
    return permission in role_permissions


class PermissionChecker:
    """按**权限位**准入的依赖（``check_permission`` 的唯一生产消费者）。

    A.88 配置接线②：``PERMISSIONS`` / ``check_permission`` 此前零生产调用，
    真正的准入只有 ``RoleChecker`` 的角色白名单。本类把两者接起来：

    * 判定**只**走 :func:`check_permission`（不另抄一份权限表）；
    * 403 的 status 与 detail 与 :class:`RoleChecker` **逐字一致**
      （``"You do not have permission to access this resource"``）——
      既有可观测行为与前端错误码映射（403 → E1008）都不变；
    * **fail-closed**：无 ``role`` / 角色未登记 / 权限未登记 → 一律 403。
      （旧口径下这些请求本来就被 ``RoleChecker`` 挡住，所以这不是收紧，
      而是把"凭什么是它挡住"从角色名换成权限位。）

    用法（作为端点参数默认值，不改变函数体的任何逻辑）::

        async def sign_report(
            ...,
            _perm: dict = Depends(PermissionChecker("sign_report")),
        ): ...
    """

    def __init__(self, permission: str) -> None:
        self.permission = permission

    def __call__(self, user: dict[str, Any] = Depends(get_current_active_user)) -> dict[str, Any]:
        if not check_permission(self.permission, user):
            logger.warning(
                "permission_denied",
                permission=self.permission,
                role=user.get("role") if isinstance(user, dict) else None,
                user_id=user.get("user_id") if isinstance(user, dict) else None,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource",
            )
        return user
