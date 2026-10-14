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
    "ULTRASOUND_DOCTOR": ["submit_diagnosis", "view_results", "submit_feedback"],
    "EMERGENCY_DOCTOR": ["submit_diagnosis", "view_results"],
    "DEPARTMENT_HEAD": ["submit_diagnosis", "view_results", "submit_feedback", "review_l3", "configure_thresholds"],
    "QUALITY_CONTROL": ["view_results", "review_l3", "view_audit_logs"],
    "SYSTEM_ADMIN": ["*"]
}

def check_permission(permission: str, user: dict[str, Any]) -> bool:
    user_role: str | None = user.get("role")
    if user_role is None:
        return False

    role_permissions: list[str] = PERMISSIONS.get(user_role, [])
    if "*" in role_permissions:
        return True
    return permission in role_permissions
