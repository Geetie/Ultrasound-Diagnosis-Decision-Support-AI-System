from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import timedelta
from app.security.auth import (
    create_access_token,
    verify_password,
    get_password_hash,
    get_current_user,
    get_current_active_user,
    RoleChecker,
    ROLES,
    PERMISSIONS,
    check_permission,
    load_or_generate_keys
)
from app.security.refresh_token import (
    create_refresh_token,
    verify_refresh_token,
    revoke_refresh_token,
    revoke_token_family,
    revoke_all_user_tokens,
    get_refresh_token_count
)
from app.config.settings import settings

router = APIRouter()

class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: Optional[str] = None
    user_info: Dict[str, Any]

class TokenData(BaseModel):
    user_id: Optional[str] = None

class User(BaseModel):
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    disabled: Optional[bool] = None
    role: Optional[str] = None

class UserInDB(User):
    hashed_password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str

FAKE_USERS_DB = {
    "doctor001": {
        "username": "doctor001",
        "full_name": "Zhang Doctor",
        "email": "zhang@hospital.com",
        "hashed_password": "$2b$12$EixZaYbB.rK4fl8x2q7Meu6Q6D2V5fF5Q5Q5Q5Q5Q5Q5Q5Q5Q5Q",
        "disabled": False,
        "role": "ULTRASOUND_DOCTOR"
    },
    "doctor002": {
        "username": "doctor002",
        "full_name": "Li Doctor",
        "email": "li@hospital.com",
        "hashed_password": "$2b$12$EixZaYbB.rK4fl8x2q7Meu6Q6D2V5fF5Q5Q5Q5Q5Q5Q5Q5Q5Q5Q",
        "disabled": False,
        "role": "EMERGENCY_DOCTOR"
    },
    "head001": {
        "username": "head001",
        "full_name": "Wang Director",
        "email": "wang@hospital.com",
        "hashed_password": "$2b$12$EixZaYbB.rK4fl8x2q7Meu6Q6D2V5fF5Q5Q5Q5Q5Q5Q5Q5Q5Q5Q",
        "disabled": False,
        "role": "DEPARTMENT_HEAD"
    },
    "admin001": {
        "username": "admin001",
        "full_name": "System Admin",
        "email": "admin@hospital.com",
        "hashed_password": "$2b$12$EixZaYbB.rK4fl8x2q7Meu6Q6D2V5fF5Q5Q5Q5Q5Q5Q5Q5Q5Q5Q",
        "disabled": False,
        "role": "SYSTEM_ADMIN"
    }
}

def get_user(db, username: str) -> Optional[UserInDB]:
    if username in db:
        user_dict = db[username]
        return UserInDB(**user_dict)
    return None

def authenticate_user(fake_db, username: str, password: str):
    user = get_user(fake_db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

@router.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    load_or_generate_keys()

    user = authenticate_user(FAKE_USERS_DB, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.jwt_expire_minutes)
    user_info = {
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "disabled": user.disabled
    }

    access_token = create_access_token(
        data={"sub": user.username, "user_info": user_info},
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "user_info": user_info
    }

@router.post("/login", response_model=Token)
async def login(login_request: LoginRequest):
    load_or_generate_keys()

    user = authenticate_user(FAKE_USERS_DB, login_request.username, login_request.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=settings.jwt_expire_minutes)
    user_info = {
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "disabled": user.disabled
    }

    access_token = create_access_token(
        data={"sub": user.username, "user_info": user_info},
        expires_delta=access_token_expires
    )

    refresh_token_str = create_refresh_token(
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
async def verify_token(current_user: Dict[str, Any] = Depends(get_current_active_user)):
    return {
        "valid": True,
        "user_id": current_user.get("user_id"),
        "username": current_user.get("username"),
        "role": current_user.get("role"),
        "message": "Token is valid"
    }

@router.post("/refresh-token", response_model=Token)
async def refresh_token(refresh_request: RefreshTokenRequest):
    load_or_generate_keys()

    token_data = verify_refresh_token(refresh_request.refresh_token)

    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_info = token_data["user_info"]
    family_id = token_data["family_id"]

    revoke_refresh_token(refresh_request.refresh_token)

    access_token_expires = timedelta(minutes=settings.jwt_expire_minutes)
    access_token = create_access_token(
        data={"sub": token_data["user_id"], "user_info": user_info},
        expires_delta=access_token_expires
    )

    new_refresh_token = create_refresh_token(
        user_id=token_data["user_id"],
        user_info=user_info,
        family_id=family_id
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "refresh_token": new_refresh_token,
        "user_info": user_info
    }

@router.post("/revoke-refresh-token")
async def revoke_token(
    refresh_request: RefreshTokenRequest,
    current_user: Dict[str, Any] = Depends(get_current_active_user)
):
    success = revoke_refresh_token(refresh_request.refresh_token)

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
async def revoke_all_tokens(current_user: Dict[str, Any] = Depends(get_current_active_user)):
    user_id = current_user.get("user_id") or current_user.get("username")

    revoked_count = revoke_all_user_tokens(user_id)

    return {
        "success": True,
        "message": f"Revoked {revoked_count} refresh tokens for user",
        "revoked_count": revoked_count
    }

@router.get("/token-stats")
async def get_token_stats(current_user: Dict[str, Any] = Depends(get_current_active_user)):
    stats = get_refresh_token_count()

    return {
        "total_tokens": stats["total"],
        "active_tokens": stats["active"],
        "revoked_tokens": stats["revoked"],
        "expired_tokens": stats["expired"]
    }

@router.get("/users/me", response_model=User)
async def read_users_me(current_user: Dict[str, Any] = Depends(get_current_active_user)):
    return User(
        username=current_user.get("username"),
        email=current_user.get("email"),
        full_name=current_user.get("full_name"),
        disabled=current_user.get("disabled"),
        role=current_user.get("role")
    )

@router.get("/roles")
async def get_roles(current_user: Dict[str, Any] = Depends(get_current_active_user)):
    return {"roles": ROLES}

@router.get("/permissions")
async def get_permissions(current_user: Dict[str, Any] = Depends(get_current_active_user)):
    user_role = current_user.get("role")
    permissions = PERMISSIONS.get(user_role, [])
    return {
        "user_role": user_role,
        "permissions": permissions
    }

@router.get("/protected/test")
async def protected_test(
    current_user: Dict[str, Any] = Depends(get_current_active_user),
    _: bool = Depends(RoleChecker(["ULTRASOUND_DOCTOR", "DEPARTMENT_HEAD"]))
):
    return {"message": "This is a protected endpoint", "user": current_user}