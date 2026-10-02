from datetime import timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select

from app.config.database import get_db_session
from app.config.settings import settings
from app.models.database import User
from app.security.auth import (
    PERMISSIONS,
    ROLES,
    RoleChecker,
    _ensure_jwt_key_exists,
    create_access_token,
    get_current_active_user,
    verify_password,
)
from app.security.refresh_token import (
    create_refresh_token,
    get_refresh_token_count,
    revoke_all_user_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_refresh_token,
)

router = APIRouter()
logger = structlog.get_logger()


class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str | None = None
    user_info: dict[str, Any]


class TokenData(BaseModel):
    user_id: str | None = None


class UserResponse(BaseModel):
    username: str
    email: str | None = None
    full_name: str | None = None
    disabled: bool | None = None
    role: str | None = None


class UserInDB(UserResponse):
    hashed_password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


async def get_user_by_username(username: str) -> UserInDB | None:
    async for db_session in get_db_session():
        result = await db_session.execute(
            select(User).where(User.username == username)
        )
        db_user = result.scalar_one_or_none()
        if db_user is None:
            return None
        return UserInDB(
            username=db_user.username,
            email=db_user.email,
            full_name=db_user.full_name,
            hashed_password=db_user.hashed_password,
            disabled=db_user.disabled,
            role=db_user.role,
        )
    return None


async def authenticate_user(username: str, password: str):
    user = await get_user_by_username(username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def _build_user_info(user) -> dict[str, Any]:
    return {
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "disabled": user.disabled,
    }


@router.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    _ensure_jwt_key_exists()

    user = await authenticate_user(form_data.username, form_data.password)
    if not user:
        logger.warning("auth_login_failed", username=form_data.username, endpoint="token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info("auth_login_success", username=user.username, endpoint="token")

    access_token_expires = timedelta(minutes=settings.jwt_expire_minutes)
    user_info = _build_user_info(user)

    access_token = create_access_token(
        data={"sub": user.username, "user_info": user_info},
        expires_delta=access_token_expires
    )

    refresh_token_str = await create_refresh_token(
        user_id=user.username,
        user_info=user_info
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "refresh_token": refresh_token_str,
        "user_info": user_info
    }


@router.post("/login", response_model=Token)
async def login(login_request: LoginRequest):
    _ensure_jwt_key_exists()

    user = await authenticate_user(login_request.username, login_request.password)
    if not user:
        logger.warning("auth_login_failed", username=login_request.username, endpoint="login")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.info("auth_login_success", username=user.username, endpoint="login")

    access_token_expires = timedelta(minutes=settings.jwt_expire_minutes)
    user_info = _build_user_info(user)

    access_token = create_access_token(
        data={"sub": user.username, "user_info": user_info},
        expires_delta=access_token_expires
    )

    refresh_token_str = await create_refresh_token(
        user_id=user.username,
        user_info=user_info
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "refresh_token": refresh_token_str,
        "user_info": user_info
    }


@router.get("/verify-token")
async def verify_token(current_user: dict[str, Any] = Depends(get_current_active_user)):
    return {
        "valid": True,
        "user_id": current_user.get("user_id"),
        "username": current_user.get("username"),
        "role": current_user.get("role"),
        "message": "Token is valid"
    }


@router.post("/refresh-token", response_model=Token)
async def refresh_token(refresh_request: RefreshTokenRequest):
    _ensure_jwt_key_exists()

    try:
        new_token_id = await rotate_refresh_token(refresh_request.refresh_token)
    except ValueError as e:
        logger.warning("refresh_token_rotation_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    token_data = await verify_refresh_token(new_token_id)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to verify rotated token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_info = token_data["user_info"]

    access_token_expires = timedelta(minutes=settings.jwt_expire_minutes)
    access_token = create_access_token(
        data={"sub": token_data["user_id"], "user_info": user_info},
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "refresh_token": new_token_id,
        "user_info": user_info
    }


@router.post("/revoke-refresh-token")
async def revoke_token(
    refresh_request: RefreshTokenRequest,
    current_user: dict[str, Any] = Depends(get_current_active_user)
):
    token_data = await verify_refresh_token(refresh_request.refresh_token)
    if token_data:
        token_owner = token_data.get("user_id")
        current_user_id = current_user.get("user_id") or current_user.get("username")
        if token_owner != current_user_id and "SYSTEM_ADMIN" not in (current_user.get("role"),):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot revoke other user's token"
            )

    success = await revoke_refresh_token(refresh_request.refresh_token)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to revoke token"
        )

    return {
        "success": True,
        "message": "Refresh token revoked successfully"
    }


@router.post("/revoke-all-tokens")
async def revoke_all_tokens(current_user: dict[str, Any] = Depends(get_current_active_user)):
    user_id = current_user.get("user_id") or current_user.get("username")

    revoked_count = await revoke_all_user_tokens(user_id)

    return {
        "success": True,
        "message": f"Revoked {revoked_count} refresh tokens for user",
        "revoked_count": revoked_count
    }


@router.get("/token-stats")
async def get_token_stats(
    current_user: dict[str, Any] = Depends(
        RoleChecker(["SYSTEM_ADMIN"])
    )
):
    stats = await get_refresh_token_count()

    return {
        "total_tokens": stats["total"],
        "active_tokens": stats["active"],
        "revoked_tokens": stats["revoked"],
        "expired_tokens": stats["expired"]
    }


@router.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user: dict[str, Any] = Depends(get_current_active_user)):
    return UserResponse(
        username=current_user.get("username"),
        email=current_user.get("email"),
        full_name=current_user.get("full_name"),
        disabled=current_user.get("disabled"),
        role=current_user.get("role")
    )


@router.get("/roles")
async def get_roles(current_user: dict[str, Any] = Depends(get_current_active_user)):
    return {"roles": ROLES}


@router.get("/permissions")
async def get_permissions(current_user: dict[str, Any] = Depends(get_current_active_user)):
    user_role = current_user.get("role")
    permissions = PERMISSIONS.get(user_role, [])
    return {
        "user_role": user_role,
        "permissions": permissions
    }


@router.get("/protected/test")
async def protected_test(
    current_user: dict[str, Any] = Depends(get_current_active_user),
    _: dict[str, Any] = Depends(RoleChecker(["ULTRASOUND_DOCTOR", "DEPARTMENT_HEAD"]))
):
    return {"message": "This is a protected endpoint", "user": current_user}


class NavTokenExchangeRequest(BaseModel):
    session_id: str


@router.post("/nav-token")
async def exchange_nav_token(
    request: NavTokenExchangeRequest,
    current_user: dict[str, Any] = Depends(get_current_active_user),
):
    """JWT → 导航 WS nav token 交换端点（审计 P0-2）。

    前端持 JWT 调用本端点换取 ``session_id`` 对应的 nav token，随后用
    ``{type:'auth', session_id, token}`` 完成 /api/v1/realtime/nav 的
    token 认证路径（nav.py validate_token）。token 由与 WS 校验同源的
    模块级 ConnectionManager 单例签发（AuthTokenManager._tokens），
    生命周期与 WS 侧完全一致（TTL 见
    settings.navigation_ws_auth_token_ttl_seconds）。

    不做 session 存在性校验：与 WS token 路径同口径——token 只在其
    session_id 上有效，误签发的 token 会随 TTL 过期被清理。
    """
    if not request.session_id or not request.session_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="session_id is required",
        )
    # 函数内导入：nav.py 依赖较重，且避免任何潜在导入环
    from app.api.websocket.nav import manager as nav_manager

    try:
        nav_token = await nav_manager.generate_auth_token(request.session_id)
    except Exception as exc:
        logger.warning(
            "nav_token_exchange_failed",
            session_id=request.session_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to issue navigation token",
        ) from exc

    logger.info(
        "nav_token_exchanged",
        session_id=request.session_id,
        operator=current_user.get("username"),
    )
    return {
        "session_id": request.session_id,
        "nav_token": nav_token,
        "token_type": "nav_ws",
        "expires_in": settings.navigation_ws_auth_token_ttl_seconds,
    }
