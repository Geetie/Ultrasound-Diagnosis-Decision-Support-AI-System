import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.config.redis_keys import RedisKey
from app.config.settings import settings

logger = structlog.get_logger()

class RefreshTokenStore:
    def create_refresh_token(
        self,
        user_id: str,
        user_info: dict[str, Any],
        family_id: str | None = None
    ) -> str:
        raise NotImplementedError

    def verify_refresh_token(self, token_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def rotate_refresh_token(self, old_token_id: str) -> str:
        raise NotImplementedError

    def revoke_refresh_token(self, token_id: str) -> bool:
        raise NotImplementedError

    def revoke_token_family(self, family_id: str) -> int:
        raise NotImplementedError

    def revoke_all_user_tokens(self, user_id: str) -> int:
        raise NotImplementedError

    def cleanup_expired_tokens(self) -> int:
        raise NotImplementedError

    def get_token_count(self) -> dict[str, int]:
        raise NotImplementedError

    def get_user_token_count(self, user_id: str) -> int:
        raise NotImplementedError

    def revoke_oldest_user_token(self, user_id: str) -> bool:
        raise NotImplementedError


class MemoryRefreshTokenStore(RefreshTokenStore):
    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._store: dict[str, dict[str, Any]] = {}
                cls._instance._family_store: dict[str, str] = {}
                cls._instance._user_tokens: dict[str, list[str]] = {}
                cls._instance._lock = threading.RLock()
        return cls._instance

    def create_refresh_token(
        self,
        user_id: str,
        user_info: dict[str, Any],
        family_id: str | None = None
    ) -> str:
        with self._lock:
            # 检查并限制并发会话数
            self._enforce_concurrent_session_limit(user_id)

            token_id = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)

            if family_id is None:
                family_id = secrets.token_urlsafe(16)

            token_data = {
                "token_id": token_id,
                "user_id": user_id,
                "user_info": user_info,
                "family_id": family_id,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=settings.jwt_refresh_expire_days)).isoformat(),
                "revoked": False,
                "revoked_at": None,
                "last_used_at": None
            }

            self._store[token_id] = token_data
            self._family_store[token_id] = family_id

            if user_id not in self._user_tokens:
                self._user_tokens[user_id] = []
            self._user_tokens[user_id].append(token_id)

            logger.info(
                "refresh_token_created",
                token_id=token_id[:8] + "...",
                user_id=user_id,
                family_id=family_id,
                expires_at=token_data["expires_at"]
            )

            return token_id

    def _enforce_concurrent_session_limit(self, user_id: str) -> None:
        """强制并发会话限制，超过限制时撤销最旧的token"""
        if user_id not in self._user_tokens:
            return

        current_count = self.get_user_token_count(user_id)
        if current_count >= settings.max_concurrent_sessions_per_user:
            self.revoke_oldest_user_token(user_id)

    def verify_refresh_token(self, token_id: str) -> dict[str, Any] | None:
        with self._lock:
            token_data = self._store.get(token_id)

            if token_data is None:
                family_id = self._family_store.get(token_id)
                if family_id:
                    logger.warning(
                        "refresh_token_reuse_detected",
                        token_id=token_id[:8] + "...",
                        family_id=family_id,
                        action="revoking_entire_family"
                    )
                    self.revoke_token_family(family_id)
                return None

            if token_data["revoked"]:
                family_id = token_data.get("family_id")
                if family_id:
                    logger.warning(
                        "refresh_token_reuse_detected",
                        token_id=token_id[:8] + "...",
                        family_id=family_id,
                        action="revoking_entire_family"
                    )
                    self.revoke_token_family(family_id)
                return None

            now = datetime.now(timezone.utc)
            expires_at = datetime.fromisoformat(token_data["expires_at"])
            if now > expires_at:
                logger.warning("refresh_token_expired", token_id=token_id[:8] + "...")
                return None

            token_data["last_used_at"] = now.isoformat()

            return token_data.copy()

    def rotate_refresh_token(self, old_token_id: str) -> str:
        with self._lock:
            old_data = self._store.get(old_token_id)

            if old_data is None:
                family_id = self._family_store.get(old_token_id)
                if family_id:
                    logger.warning(
                        "refresh_token_reuse_on_rotate",
                        token_id=old_token_id[:8] + "...",
                        family_id=family_id,
                        action="revoking_entire_family"
                    )
                    self.revoke_token_family(family_id)
                    raise ValueError(f"Refresh token has already been used, family {family_id} revoked")
                raise ValueError(f"Refresh token not found: {old_token_id}")

            if old_data["revoked"]:
                family_id = old_data.get("family_id")
                if family_id:
                    self.revoke_token_family(family_id)
                    raise ValueError(f"Refresh token has already been used, family {family_id} revoked")
                raise ValueError(f"Refresh token already revoked: {old_token_id}")

            old_data["revoked"] = True
            old_data["revoked_at"] = datetime.now(timezone.utc).isoformat()

            user_id = old_data["user_id"]
            family_id = old_data["family_id"]
            user_info = old_data.get("user_info", {})

            if user_id in self._user_tokens and old_token_id in self._user_tokens[user_id]:
                self._user_tokens[user_id].remove(old_token_id)

            new_token_id = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            new_data = {
                "token_id": new_token_id,
                "user_id": user_id,
                "user_info": user_info,
                "family_id": family_id,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=settings.jwt_refresh_expire_days)).isoformat(),
                "revoked": False,
                "revoked_at": None,
                "last_used_at": None,
            }

            self._store[new_token_id] = new_data
            self._family_store[new_token_id] = family_id

            if user_id not in self._user_tokens:
                self._user_tokens[user_id] = []
            self._user_tokens[user_id].append(new_token_id)

            logger.info(
                "refresh_token_rotated",
                old_token_id=old_token_id[:8] + "...",
                new_token_id=new_token_id[:8] + "...",
                family_id=family_id,
                user_id=user_id,
            )

            return new_token_id

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
            token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()

            user_id = token_data["user_id"]
            if user_id in self._user_tokens and token_id in self._user_tokens[user_id]:
                self._user_tokens[user_id].remove(token_id)

            logger.info("refresh_token_revoked", token_id=token_id[:8] + "...", user_id=user_id)

            return True

    def revoke_token_family(self, family_id: str) -> int:
        with self._lock:
            revoked_count = 0

            for token_id, token_data in list(self._store.items()):
                if not isinstance(token_data, dict) or "family_id" not in token_data:
                    continue
                if token_data["family_id"] == family_id and not token_data["revoked"]:
                    token_data["revoked"] = True
                    token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()

                    user_id = token_data["user_id"]
                    if user_id in self._user_tokens and token_id in self._user_tokens[user_id]:
                        self._user_tokens[user_id].remove(token_id)

                    revoked_count += 1

            logger.info("refresh_token_family_revoked", family_id=family_id, count=revoked_count)

            return revoked_count

    def revoke_all_user_tokens(self, user_id: str) -> int:
        with self._lock:
            revoked_count = 0

            if user_id not in self._user_tokens:
                return 0

            token_ids = list(self._user_tokens[user_id])
            for token_id in token_ids:
                token_data = self._store.get(token_id)
                if token_data and not token_data["revoked"]:
                    token_data["revoked"] = True
                    token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()
                    revoked_count += 1

            self._user_tokens[user_id] = []

            logger.info("all_user_refresh_tokens_revoked", user_id=user_id, count=revoked_count)

            return revoked_count

    def cleanup_expired_tokens(self) -> int:
        with self._lock:
            now = datetime.now(timezone.utc)
            expired_tokens = []

            for token_id, data in self._store.items():
                if not isinstance(data, dict) or "expires_at" not in data:
                    continue
                expires_at = datetime.fromisoformat(data["expires_at"])
                if now > expires_at:
                    expired_tokens.append(token_id)

            for token_id in expired_tokens:
                token_data = self._store.get(token_id)
                if token_data:
                    user_id = token_data["user_id"]
                    if user_id in self._user_tokens and token_id in self._user_tokens[user_id]:
                        self._user_tokens[user_id].remove(token_id)

                    del self._store[token_id]
                    if token_id in self._family_store:
                        del self._family_store[token_id]

            if expired_tokens:
                logger.info("expired_tokens_cleaned", count=len(expired_tokens))

            return len(expired_tokens)

    def get_token_count(self) -> dict[str, int]:
        with self._lock:
            now = datetime.now(timezone.utc)
            total = 0
            active = 0
            revoked = 0
            expired = 0

            for key, data in self._store.items():
                if not isinstance(data, dict) or "revoked" not in data:
                    continue
                total += 1
                if data["revoked"]:
                    revoked += 1
                else:
                    expires_at = datetime.fromisoformat(data["expires_at"])
                    if now > expires_at:
                        expired += 1
                    else:
                        active += 1

            return {
                "total": total,
                "active": active,
                "revoked": revoked,
                "expired": expired
            }

    def get_user_token_count(self, user_id: str) -> int:
        with self._lock:
            if user_id not in self._user_tokens:
                return 0

            now = datetime.now(timezone.utc)
            count = 0
            for token_id in self._user_tokens[user_id]:
                token_data = self._store.get(token_id)
                if token_data and not token_data["revoked"]:
                    expires_at = datetime.fromisoformat(token_data["expires_at"])
                    if now <= expires_at:
                        count += 1
            return count

    def revoke_oldest_user_token(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._user_tokens or not self._user_tokens[user_id]:
                return False

            oldest_token = None
            oldest_time = None

            for token_id in self._user_tokens[user_id]:
                token_data = self._store.get(token_id)
                if token_data and not token_data["revoked"]:
                    created_at = datetime.fromisoformat(token_data["created_at"])
                    if oldest_time is None or created_at < oldest_time:
                        oldest_time = created_at
                        oldest_token = token_id

            if oldest_token:
                return self.revoke_refresh_token(oldest_token)

            return False


class RedisRefreshTokenStore(RefreshTokenStore):
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.RLock()
            cls._instance._redis_client = None
        return cls._instance

    async def _get_redis_client(self):
        if self._redis_client is None:
            from app.config.redis import RedisPool
            self._redis_client = await RedisPool.get_client()
        return self._redis_client

    async def create_refresh_token(
        self,
        user_id: str,
        user_info: dict[str, Any],
        family_id: str | None = None
    ) -> str:
        current_count = await self.get_user_token_count(user_id)
        if current_count >= settings.max_concurrent_sessions_per_user:
            logger.warning(
                "redis_token_store_concurrent_session_limit_enforced",
                user_id=user_id,
                current_count=current_count,
                limit=settings.max_concurrent_sessions_per_user,
            )
            await self.revoke_oldest_user_token(user_id)

        token_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        if family_id is None:
            family_id = secrets.token_urlsafe(16)

        token_data = {
            "token_id": token_id,
            "user_id": user_id,
            "user_info": user_info,
            "family_id": family_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=settings.jwt_refresh_expire_days)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None
        }

        redis_client = await self._get_redis_client()

        token_ttl = timedelta(days=settings.jwt_refresh_expire_days + 1)
        token_ttl_seconds = int(token_ttl.total_seconds())

        async with redis_client.pipeline(transaction=True) as pipe:
            await pipe.set(RedisKey.auth_token(token_id), json.dumps(token_data), ex=token_ttl_seconds)
            await pipe.sadd(RedisKey.auth_user_tokens(user_id), token_id)
            await pipe.expire(RedisKey.auth_user_tokens(user_id), token_ttl_seconds)
            await pipe.set(RedisKey.auth_token_family(token_id), family_id, ex=token_ttl_seconds)
            await pipe.sadd(RedisKey.auth_family_tokens(family_id), token_id)
            await pipe.expire(RedisKey.auth_family_tokens(family_id), token_ttl_seconds)
            await pipe.execute()

        logger.info(
            "refresh_token_created_redis",
            token_id=token_id[:8] + "...",
            user_id=user_id,
            family_id=family_id
        )

        return token_id

    async def verify_refresh_token(self, token_id: str) -> dict[str, Any] | None:
        redis_client = await self._get_redis_client()

        token_data_str = await redis_client.get(RedisKey.auth_token(token_id))
        if not token_data_str:
            family_id_str = await redis_client.get(RedisKey.auth_token_family(token_id))
            if family_id_str:
                family_id = family_id_str if isinstance(family_id_str, str) else family_id_str.decode()
                family_already_revoked = await redis_client.exists(RedisKey.auth_family_revoked(family_id))
                if family_already_revoked:
                    logger.warning(
                        "refresh_token_reuse_in_revoked_family_redis",
                        token_id=token_id[:8] + "...",
                        family_id=family_id,
                        action="family_already_revoked"
                    )
                else:
                    logger.warning(
                        "refresh_token_reuse_detected_redis",
                        token_id=token_id[:8] + "...",
                        family_id=family_id,
                        action="revoking_entire_family"
                    )
                    await self.revoke_token_family(family_id)
            else:
                logger.warning("refresh_token_not_found_redis", token_id=token_id[:8] + "...")
            return None

        try:
            token_data = json.loads(token_data_str)
        except json.JSONDecodeError:
            logger.error("refresh_token_data_corrupted", token_id=token_id[:8] + "...")
            return None

        family_id = token_data.get("family_id")
        if family_id:
            family_already_revoked = await redis_client.exists(RedisKey.auth_family_revoked(family_id))
            if family_already_revoked:
                logger.warning(
                    "refresh_token_in_revoked_family_redis",
                    token_id=token_id[:8] + "...",
                    family_id=family_id,
                    action="family_already_revoked"
                )
                return None

        if token_data.get("revoked", False):
            if family_id:
                logger.warning(
                    "refresh_token_reuse_detected_redis",
                    token_id=token_id[:8] + "...",
                    family_id=family_id,
                    action="revoking_entire_family"
                )
                await self.revoke_token_family(family_id)
            return None

        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(token_data["expires_at"])
        if now > expires_at:
            logger.warning("refresh_token_expired_redis", token_id=token_id[:8] + "...")
            await redis_client.delete(RedisKey.auth_token(token_id))
            await redis_client.srem(RedisKey.auth_user_tokens(token_data['user_id']), token_id)
            return None

        token_data["last_used_at"] = now.isoformat()
        try:
            _UPDATE_LAST_USED_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current then
    local data = cjson.decode(current)
    data['last_used_at'] = ARGV[1]
    local ttl = redis.call('TTL', KEYS[1])
    if ttl > 0 then
        redis.call('SETEX', KEYS[1], ttl, cjson.encode(data))
    end
    return 1
end
return 0
"""
            await redis_client.eval(
                _UPDATE_LAST_USED_SCRIPT,
                1,
                RedisKey.auth_token(token_id),
                now.isoformat(),
            )
        except Exception as e:
            logger.debug("failed_to_update_last_used_at", token_id=token_id[:8] + "...", error=str(e))

        return token_data.copy()

    async def rotate_refresh_token(self, old_token_id: str) -> str:
        redis_client = await self._get_redis_client()

        token_data_str = await redis_client.get(RedisKey.auth_token(old_token_id))

        if not token_data_str:
            family_id_str = await redis_client.get(RedisKey.auth_token_family(old_token_id))
            if family_id_str:
                family_id = family_id_str if isinstance(family_id_str, str) else family_id_str.decode()
                logger.warning(
                    "refresh_token_reuse_on_rotate_redis",
                    token_id=old_token_id[:8] + "...",
                    family_id=family_id,
                    action="revoking_entire_family"
                )
                await self.revoke_token_family(family_id)
                raise ValueError(f"Refresh token has already been used, family {family_id} revoked")
            raise ValueError(f"Refresh token not found: {old_token_id}")

        try:
            old_data = json.loads(token_data_str)
        except json.JSONDecodeError:
            raise ValueError(f"Refresh token data corrupted: {old_token_id}")

        if old_data.get("revoked", False):
            family_id = old_data.get("family_id")
            if family_id:
                logger.warning(
                    "refresh_token_reuse_on_rotate_redis",
                    token_id=old_token_id[:8] + "...",
                    family_id=family_id,
                    action="revoking_entire_family"
                )
                await self.revoke_token_family(family_id)
                raise ValueError(f"Refresh token has already been used, family {family_id} revoked")
            raise ValueError(f"Refresh token already revoked: {old_token_id}")

        now = datetime.now(timezone.utc)
        expires_at = datetime.fromisoformat(old_data["expires_at"])
        if now > expires_at:
            raise ValueError(f"Refresh token expired: {old_token_id}")

        old_data["revoked"] = True
        old_data["revoked_at"] = now.isoformat()

        user_id = old_data["user_id"]
        family_id = old_data["family_id"]
        user_info = old_data.get("user_info", {})

        new_token_id = secrets.token_urlsafe(32)
        new_data = {
            "token_id": new_token_id,
            "user_id": user_id,
            "user_info": user_info,
            "family_id": family_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=settings.jwt_refresh_expire_days)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }

        token_ttl = timedelta(days=settings.jwt_refresh_expire_days + 1)
        token_ttl_seconds = int(token_ttl.total_seconds())

        async with redis_client.pipeline(transaction=True) as pipe:
            ttl_val = await redis_client.ttl(RedisKey.auth_token(old_token_id))
            if ttl_val and ttl_val > 0:
                await pipe.setex(RedisKey.auth_token(old_token_id), ttl_val, json.dumps(old_data))
            else:
                await pipe.set(RedisKey.auth_token(old_token_id), json.dumps(old_data))
            await pipe.srem(RedisKey.auth_user_tokens(user_id), old_token_id)
            await pipe.set(RedisKey.auth_token(new_token_id), json.dumps(new_data), ex=token_ttl_seconds)
            await pipe.sadd(RedisKey.auth_user_tokens(user_id), new_token_id)
            await pipe.expire(RedisKey.auth_user_tokens(user_id), token_ttl_seconds)
            await pipe.set(RedisKey.auth_token_family(new_token_id), family_id, ex=token_ttl_seconds)
            await pipe.sadd(RedisKey.auth_family_tokens(family_id), new_token_id)
            await pipe.expire(RedisKey.auth_family_tokens(family_id), token_ttl_seconds)
            await pipe.execute()

        logger.info(
            "refresh_token_rotated_redis",
            old_token_id=old_token_id[:8] + "...",
            new_token_id=new_token_id[:8] + "...",
            family_id=family_id,
            user_id=user_id,
        )

        return new_token_id

    async def revoke_refresh_token(self, token_id: str) -> bool:
        redis_client = await self._get_redis_client()

        token_data_str = await redis_client.get(RedisKey.auth_token(token_id))
        if not token_data_str:
            logger.warning("refresh_token_not_found_for_revoke_redis", token_id=token_id[:8] + "...")
            return False

        try:
            token_data = json.loads(token_data_str)
        except json.JSONDecodeError:
            logger.error("refresh_token_data_corrupted_for_revoke", token_id=token_id[:8] + "...")
            return False

        if token_data.get("revoked", False):
            logger.warning("refresh_token_already_revoked_redis", token_id=token_id[:8] + "...")
            return True

        token_data["revoked"] = True
        token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()

        user_id = token_data["user_id"]
        family_id = token_data["family_id"]

        async with redis_client.pipeline(transaction=True) as pipe:
            ttl = await redis_client.ttl(RedisKey.auth_token(token_id))
            if ttl > 0:
                await pipe.setex(RedisKey.auth_token(token_id), ttl, json.dumps(token_data))
            else:
                await pipe.set(RedisKey.auth_token(token_id), json.dumps(token_data))
            await pipe.srem(RedisKey.auth_user_tokens(user_id), token_id)
            await pipe.srem(RedisKey.auth_family_tokens(family_id), token_id)
            await pipe.execute()

        logger.info("refresh_token_revoked_redis", token_id=token_id[:8] + "...", user_id=user_id)

        return True

    async def revoke_token_family(self, family_id: str) -> int:
        redis_client = await self._get_redis_client()

        token_ids_raw = await redis_client.smembers(RedisKey.auth_family_tokens(family_id))
        token_ids = [t.decode() if isinstance(t, bytes) else t for t in token_ids_raw]

        if not token_ids:
            return 0

        pipe = redis_client.pipeline()
        for token_id in token_ids:
            pipe.get(RedisKey.auth_token(token_id))
            pipe.ttl(RedisKey.auth_token(token_id))
        results = await pipe.execute()

        revoked_count = 0
        update_pipe = redis_client.pipeline(transaction=True)

        for i, token_id in enumerate(token_ids):
            token_data_str = results[i * 2]
            ttl_val = results[i * 2 + 1]
            if token_data_str:
                try:
                    token_data = json.loads(token_data_str)
                    if not token_data.get("revoked", False):
                        token_data["revoked"] = True
                        token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()
                        if ttl_val and ttl_val > 0:
                            update_pipe.setex(RedisKey.auth_token(token_id), ttl_val, json.dumps(token_data))
                        else:
                            update_pipe.set(RedisKey.auth_token(token_id), json.dumps(token_data))
                        update_pipe.srem(RedisKey.auth_user_tokens(token_data['user_id']), token_id)
                        revoked_count += 1
                except json.JSONDecodeError:
                    pass

        family_revoked_ttl = int(timedelta(days=settings.jwt_refresh_expire_days + 1).total_seconds())
        update_pipe.set(RedisKey.auth_family_revoked(family_id), "1", ex=family_revoked_ttl)
        if revoked_count > 0:
            await update_pipe.execute()
        else:
            await redis_client.set(RedisKey.auth_family_revoked(family_id), "1", ex=family_revoked_ttl)

        logger.info("refresh_token_family_revoked_redis", family_id=family_id, count=revoked_count)

        return revoked_count

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        redis_client = await self._get_redis_client()

        token_ids_raw = await redis_client.smembers(RedisKey.auth_user_tokens(user_id))
        token_ids = [t.decode() if isinstance(t, bytes) else t for t in token_ids_raw]

        if not token_ids:
            return 0

        pipe = redis_client.pipeline()
        for token_id in token_ids:
            pipe.get(RedisKey.auth_token(token_id))
            pipe.ttl(RedisKey.auth_token(token_id))
        results = await pipe.execute()

        revoked_count = 0
        update_pipe = redis_client.pipeline(transaction=True)

        for i, token_id in enumerate(token_ids):
            token_data_str = results[i * 2]
            ttl_val = results[i * 2 + 1]
            if token_data_str:
                try:
                    token_data = json.loads(token_data_str)
                    if not token_data.get("revoked", False):
                        token_data["revoked"] = True
                        token_data["revoked_at"] = datetime.now(timezone.utc).isoformat()
                        family_id = token_data["family_id"]
                        if ttl_val and ttl_val > 0:
                            update_pipe.setex(RedisKey.auth_token(token_id), ttl_val, json.dumps(token_data))
                        else:
                            update_pipe.set(RedisKey.auth_token(token_id), json.dumps(token_data))
                        update_pipe.srem(RedisKey.auth_family_tokens(family_id), token_id)
                        revoked_count += 1
                except json.JSONDecodeError:
                    pass

        update_pipe.delete(RedisKey.auth_user_tokens(user_id))
        if revoked_count > 0:
            await update_pipe.execute()
        else:
            await redis_client.delete(RedisKey.auth_user_tokens(user_id))

        logger.info("all_user_refresh_tokens_revoked_redis", user_id=user_id, count=revoked_count)

        return revoked_count

    async def cleanup_expired_tokens(self) -> int:
        redis_client = await self._get_redis_client()

        now = datetime.now(timezone.utc)
        expired_count = 0

        async for key in redis_client.scan_iter(RedisKey.AUTH_TOKEN_PATTERN, count=100):
            token_data_str = await redis_client.get(key)
            if token_data_str:
                try:
                    token_data = json.loads(token_data_str)
                    expires_at = datetime.fromisoformat(token_data["expires_at"])
                    if now > expires_at:
                        key_str = key if isinstance(key, str) else key.decode()
                        token_id = key_str.split(":")[-1]
                        user_id = token_data["user_id"]
                        family_id = token_data["family_id"]

                        async with redis_client.pipeline(transaction=True) as pipe:
                            await pipe.delete(key)
                            await pipe.srem(RedisKey.auth_user_tokens(user_id), token_id)
                            await pipe.srem(RedisKey.auth_family_tokens(family_id), token_id)
                            await pipe.execute()

                        expired_count += 1
                except (json.JSONDecodeError, KeyError):
                    await redis_client.delete(key)

        logger.info("expired_tokens_cleaned_redis", count=expired_count)

        return expired_count

    async def get_token_count(self) -> dict[str, int]:
        redis_client = await self._get_redis_client()

        all_keys = []
        async for key in redis_client.scan_iter(RedisKey.AUTH_TOKEN_PATTERN, count=100):
            all_keys.append(key)

        if not all_keys:
            return {"total": 0, "active": 0, "revoked": 0, "expired": 0}

        pipe = redis_client.pipeline()
        for key in all_keys:
            pipe.get(key)
        results = await pipe.execute()

        now = datetime.now(timezone.utc)
        total = len(all_keys)
        active = 0
        revoked = 0
        expired = 0

        for token_data_str in results:
            if token_data_str:
                try:
                    token_data = json.loads(token_data_str)
                    if token_data.get("revoked", False):
                        revoked += 1
                    else:
                        expires_at = datetime.fromisoformat(token_data["expires_at"])
                        if now > expires_at:
                            expired += 1
                        else:
                            active += 1
                except json.JSONDecodeError:
                    pass

        return {
            "total": total,
            "active": active,
            "revoked": revoked,
            "expired": expired
        }

    async def get_user_token_count(self, user_id: str) -> int:
        redis_client = await self._get_redis_client()

        token_ids_raw = await redis_client.smembers(RedisKey.auth_user_tokens(user_id))
        token_ids = [t.decode() if isinstance(t, bytes) else t for t in token_ids_raw]

        if not token_ids:
            return 0

        pipe = redis_client.pipeline()
        for token_id in token_ids:
            pipe.get(RedisKey.auth_token(token_id))
        results = await pipe.execute()

        now = datetime.now(timezone.utc)
        count = 0

        for token_data_str in results:
            if token_data_str:
                try:
                    token_data = json.loads(token_data_str)
                    if not token_data.get("revoked", False):
                        expires_at = datetime.fromisoformat(token_data["expires_at"])
                        if now <= expires_at:
                            count += 1
                except json.JSONDecodeError:
                    pass

        return count

    async def revoke_oldest_user_token(self, user_id: str) -> bool:
        redis_client = await self._get_redis_client()

        token_ids_raw = await redis_client.smembers(RedisKey.auth_user_tokens(user_id))
        token_ids = [t.decode() if isinstance(t, bytes) else t for t in token_ids_raw]

        if not token_ids:
            return False

        pipe = redis_client.pipeline()
        for token_id in token_ids:
            pipe.get(RedisKey.auth_token(token_id))
        results = await pipe.execute()

        oldest_token = None
        oldest_time = None

        for token_id, token_data_str in zip(token_ids, results):
            if token_data_str:
                try:
                    token_data = json.loads(token_data_str)
                    if not token_data.get("revoked", False):
                        created_at = datetime.fromisoformat(token_data["created_at"])
                        if oldest_time is None or created_at < oldest_time:
                            oldest_time = created_at
                            oldest_token = token_id
                except json.JSONDecodeError:
                    pass

        if oldest_token:
            return await self.revoke_refresh_token(oldest_token)

        return False


_use_redis = os.environ.get("USE_REDIS_STORAGE", "false").lower() == "true"

if _use_redis:
    refresh_token_store = RedisRefreshTokenStore()
else:
    refresh_token_store = MemoryRefreshTokenStore()


async def create_refresh_token(user_id: str, user_info: dict[str, Any], family_id: str | None = None) -> str:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.create_refresh_token(user_id, user_info, family_id)
    else:
        return refresh_token_store.create_refresh_token(user_id, user_info, family_id)


async def verify_refresh_token(token_id: str) -> dict[str, Any] | None:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.verify_refresh_token(token_id)
    else:
        return refresh_token_store.verify_refresh_token(token_id)


async def revoke_refresh_token(token_id: str) -> bool:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.revoke_refresh_token(token_id)
    else:
        return refresh_token_store.revoke_refresh_token(token_id)


async def rotate_refresh_token(old_token_id: str) -> str:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.rotate_refresh_token(old_token_id)
    else:
        return refresh_token_store.rotate_refresh_token(old_token_id)


async def revoke_token_family(family_id: str) -> int:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.revoke_token_family(family_id)
    else:
        return refresh_token_store.revoke_token_family(family_id)


async def revoke_all_user_tokens(user_id: str) -> int:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.revoke_all_user_tokens(user_id)
    else:
        return refresh_token_store.revoke_all_user_tokens(user_id)


async def cleanup_expired_tokens() -> int:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.cleanup_expired_tokens()
    else:
        return refresh_token_store.cleanup_expired_tokens()


async def get_refresh_token_count() -> dict[str, int]:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.get_token_count()
    else:
        return refresh_token_store.get_token_count()


async def get_user_token_count(user_id: str) -> int:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.get_user_token_count(user_id)
    else:
        return refresh_token_store.get_user_token_count(user_id)


async def revoke_oldest_user_token(user_id: str) -> bool:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return await refresh_token_store.revoke_oldest_user_token(user_id)
    else:
        return refresh_token_store.revoke_oldest_user_token(user_id)


def reset_storage() -> None:
    global refresh_token_store
    MemoryRefreshTokenStore._instance = None
    if not _use_redis:
        refresh_token_store = MemoryRefreshTokenStore()


def get_storage_mode() -> str:
    if isinstance(refresh_token_store, RedisRefreshTokenStore):
        return "redis"
    return "memory"
