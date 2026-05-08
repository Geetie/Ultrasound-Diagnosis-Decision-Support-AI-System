from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from jose import JWTError, jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import bcrypt
from app.config.settings import settings
import os
import structlog
import threading
import secrets

logger = structlog.get_logger()

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

PRIVATE_KEY_PATH: str = os.environ.get("JWT_PRIVATE_KEY_PATH", "keys/jwt_private.pem")
PUBLIC_KEY_PATH: str = os.environ.get("JWT_PUBLIC_KEY_PATH", "keys/jwt_public.pem")

private_key: Optional[rsa.RSAPrivateKey] = None
public_key: Optional[rsa.RSAPublicKey] = None
_key_lock: threading.RLock = threading.RLock()

def _generate_rsa_key_pair(bits: int = 2048) -> tuple:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=bits,
        backend=default_backend()
    )
    public_key = private_key.public_key()
    return private_key, public_key

def _load_or_generate_keys() -> None:
    global private_key, public_key

    with _key_lock:
        if private_key is not None and public_key is not None:
            return

        if os.path.exists(PRIVATE_KEY_PATH) and os.path.exists(PUBLIC_KEY_PATH):
            try:
                with open(PRIVATE_KEY_PATH, "rb") as f:
                    private_key = serialization.load_pem_private_key(
                        f.read(),
                        password=None,
                        backend=default_backend()
                    )
                with open(PUBLIC_KEY_PATH, "rb") as f:
                    public_key = serialization.load_pem_public_key(
                        f.read(),
                        backend=default_backend()
                    )
                logger.info("jwt_keys_loaded_from_disk", private_path=PRIVATE_KEY_PATH)
            except Exception as e:
                logger.error("jwt_key_load_failed", error=str(e))
                private_key, public_key = _generate_rsa_key_pair()
                _save_keys_to_disk(private_key, public_key)
        else:
            private_key, public_key = _generate_rsa_key_pair()
            _save_keys_to_disk(private_key, public_key)
            logger.info("jwt_keys_generated_and_saved", private_path=PRIVATE_KEY_PATH)

def load_or_generate_keys() -> None:
    _load_or_generate_keys()


def _save_keys_to_disk(private_key: rsa.RSAPrivateKey, public_key: rsa.RSAPublicKey) -> None:
    try:
        key_dir = os.path.dirname(PRIVATE_KEY_PATH)
        if key_dir and not os.path.exists(key_dir):
            os.makedirs(key_dir)

        with open(PRIVATE_KEY_PATH, 'wb') as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))

        with open(PUBLIC_KEY_PATH, 'wb') as f:
            f.write(public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ))
        logger.info("jwt_keys_saved_to_disk", private_path=PRIVATE_KEY_PATH)
    except Exception as e:
        logger.error("jwt_key_save_failed", error=str(e))

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    _load_or_generate_keys()

    with _key_lock:
        to_encode: Dict[str, Any] = data.copy()
        if expires_delta:
            expire: datetime = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)

        now = datetime.now(timezone.utc)
        to_encode.update({
            "exp": expire,
            "iat": now,
            "jti": secrets.token_urlsafe(16)
        })

        if private_key is None:
            raise ValueError("Private key not initialized")
        
        # Convert private key to PEM string
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

        encoded_jwt: str = jwt.encode(
            to_encode,
            private_key_pem,
            algorithm=settings.jwt_algorithm
        )
        return encoded_jwt

def decode_access_token(token: str) -> Dict[str, Any]:
    _load_or_generate_keys()

    with _key_lock:
        if public_key is None:
            raise ValueError("Public key not initialized")
        
        # Convert public key to PEM string
        public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        try:
            payload: Dict[str, Any] = jwt.decode(
                token,
                public_key_pem,
                algorithms=[settings.jwt_algorithm],
                options={"verify_exp": True}
            )
            return payload
        except JWTError as e:
            logger.warning("jwt_decode_failed", error=str(e))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    payload: Dict[str, Any] = decode_access_token(token)
    user_id: Optional[str] = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_info: Dict[str, Any] = payload.get("user_info", {}).copy()
    user_info["user_id"] = user_id

    return user_info

def get_current_active_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if current_user.get("disabled", False):
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

class RoleChecker:
    def __init__(self, allowed_roles: List[str]) -> None:
        self.allowed_roles: List[str] = allowed_roles

    def __call__(self, user: Dict[str, Any] = Depends(get_current_active_user)) -> bool:
        user_role: Optional[str] = user.get("role")
        if user_role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource"
            )
        return True

ROLES: Dict[str, str] = {
    "ULTRASOUND_DOCTOR": "超声科医生",
    "EMERGENCY_DOCTOR": "急诊科医生",
    "DEPARTMENT_HEAD": "科室主任",
    "QUALITY_CONTROL": "质控管理员",
    "SYSTEM_ADMIN": "系统管理员"
}

PERMISSIONS: Dict[str, List[str]] = {
    "ULTRASOUND_DOCTOR": ["submit_diagnosis", "view_results", "submit_feedback"],
    "EMERGENCY_DOCTOR": ["submit_diagnosis", "view_results"],
    "DEPARTMENT_HEAD": ["submit_diagnosis", "view_results", "submit_feedback", "review_l3", "configure_thresholds"],
    "QUALITY_CONTROL": ["view_results", "review_l3", "view_audit_logs"],
    "SYSTEM_ADMIN": ["*"]
}

def check_permission(permission: str, user: Dict[str, Any]) -> bool:
    user_role: Optional[str] = user.get("role")
    if user_role is None:
        return False

    role_permissions: List[str] = PERMISSIONS.get(user_role, [])
    if "*" in role_permissions:
        return True
    return permission in role_permissions