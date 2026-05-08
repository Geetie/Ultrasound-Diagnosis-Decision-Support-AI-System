from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from jose import JWTError, jwt
import uuid
import structlog
import threading
import secrets

from app.config.settings import settings

logger = structlog.get_logger()

_refresh_token_store: Dict[str, Dict[str, Any]] = {}
_refresh_token_lock = threading.RLock()
_rotation_token_family_store: Dict[str, str] = {}

class RefreshTokenStore:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RefreshTokenStore, cls).__new__(cls)
            cls._instance._store: Dict[str, Dict[str, Any]] = {}
            cls._instance._family_store: Dict[str, str] = {}
            cls._instance._lock = threading.RLock()
        return cls._instance
    
    def create_refresh_token(
        self,
        user_id: str,
        user_info: Dict[str, Any],
        family_id: Optional[str] = None
    ) -> str:
        with self._lock:
            token_id = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            
            if family_id is None:
                family_id = secrets.token_urlsafe(16)
            
            token_data = {
                "token_id": token_id,
                "user_id": user_id,
                "user_info": user_info,
                "family_id": family_id,
                "created_at": now,
                "expires_at": now + timedelta(days=settings.jwt_refresh_expire_days),
                "revoked": False,
                "revoked_at": None,
                "last_used_at": None
            }
            
            self._store[token_id] = token_data
            self._family_store[token_id] = family_id
            
            logger.info(
                "refresh_token_created",
                token_id=token_id[:8] + "...",
                user_id=user_id,
                family_id=family_id,
                expires_at=token_data["expires_at"].isoformat()
            )
            
            return token_id
    
    def verify_refresh_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            token_data = self._store.get(token_id)
            
            if token_data is None:
                logger.warning("refresh_token_not_found", token_id=token_id[:8] + "...")
                return None
            
            if token_data["revoked"]:
                logger.warning("refresh_token_revoked", token_id=token_id[:8] + "...")
                return None
            
            now = datetime.now(timezone.utc)
            if now > token_data["expires_at"]:
                logger.warning("refresh_token_expired", token_id=token_id[:8] + "...")
                return None
            
            token_data["last_used_at"] = now
            
            return token_data.copy()
    
    def revoke_refresh_token(self, token_id: str) -> bool:
        with self._lock:
            token_data = self._store.get(token_id)
            
            if token_data is None:
                logger.warning("refresh_token_not_found_for_revoke", token_id=token_id[:8] + "...")
                return False
            
            if token_data["revoked"]:
                logger.warning("refresh_token_already_revoked", token_id=token_id[:8] + "...")
                return True
            
            token_data["revoked"] = True
            token_data["revoked_at"] = datetime.now(timezone.utc)
            
            logger.info("refresh_token_revoked", token_id=token_id[:8] + "...", user_id=token_data["user_id"])
            
            return True
    
    def revoke_token_family(self, family_id: str) -> int:
        with self._lock:
            revoked_count = 0
            
            for token_id, token_data in self._store.items():
                if token_data["family_id"] == family_id and not token_data["revoked"]:
                    token_data["revoked"] = True
                    token_data["revoked_at"] = datetime.now(timezone.utc)
                    revoked_count += 1
            
            logger.info("refresh_token_family_revoked", family_id=family_id, count=revoked_count)
            
            return revoked_count
    
    def revoke_all_user_tokens(self, user_id: str) -> int:
        with self._lock:
            revoked_count = 0
            
            for token_id, token_data in self._store.items():
                if token_data["user_id"] == user_id and not token_data["revoked"]:
                    token_data["revoked"] = True
                    token_data["revoked_at"] = datetime.now(timezone.utc)
                    revoked_count += 1
            
            logger.info("all_user_refresh_tokens_revoked", user_id=user_id, count=revoked_count)
            
            return revoked_count
    
    def cleanup_expired_tokens(self) -> int:
        with self._lock:
            now = datetime.now(timezone.utc)
            expired_tokens = [
                token_id for token_id, data in self._store.items()
                if now > data["expires_at"]
            ]
            
            for token_id in expired_tokens:
                family_id = self._family_store.get(token_id)
                del self._store[token_id]
                if family_id:
                    del self._family_store[token_id]
            
            if expired_tokens:
                logger.info("expired_tokens_cleaned", count=len(expired_tokens))
            
            return len(expired_tokens)
    
    def get_token_count(self) -> Dict[str, int]:
        with self._lock:
            now = datetime.now(timezone.utc)
            total = len(self._store)
            active = sum(1 for d in self._store.values() if not d["revoked"] and now <= d["expires_at"])
            revoked = sum(1 for d in self._store.values() if d["revoked"])
            expired = sum(1 for d in self._store.values() if now > d["expires_at"])
            
            return {
                "total": total,
                "active": active,
                "revoked": revoked,
                "expired": expired
            }


refresh_token_store = RefreshTokenStore()


def create_refresh_token(user_id: str, user_info: Dict[str, Any], family_id: Optional[str] = None) -> str:
    return refresh_token_store.create_refresh_token(user_id, user_info, family_id)


def verify_refresh_token(token_id: str) -> Optional[Dict[str, Any]]:
    return refresh_token_store.verify_refresh_token(token_id)


def revoke_refresh_token(token_id: str) -> bool:
    return refresh_token_store.revoke_refresh_token(token_id)


def revoke_token_family(family_id: str) -> int:
    return refresh_token_store.revoke_token_family(family_id)


def revoke_all_user_tokens(user_id: str) -> int:
    return refresh_token_store.revoke_all_user_tokens(user_id)


def cleanup_expired_tokens() -> int:
    return refresh_token_store.cleanup_expired_tokens()


def get_refresh_token_count() -> Dict[str, int]:
    return refresh_token_store.get_token_count()