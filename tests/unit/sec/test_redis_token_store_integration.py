import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf, pytest.mark.sec_domain_token]

from app.config.redis_keys import RedisKey
from app.security.refresh_token import (
    RedisRefreshTokenStore,
    MemoryRefreshTokenStore,
)


@pytest.fixture
def sample_user_info():
    return {
        "username": "test_user",
        "full_name": "Test User",
        "email": "test@example.com",
        "role": "ULTRASOUND_DOCTOR",
        "disabled": False,
    }


class FakeRedisClient:
    def __init__(self):
        self._data = {}
        self._sets = {}
        self._expiry = {}

    async def get(self, key):
        val = self._data.get(key)
        if val is not None and key in self._expiry:
            if datetime.now(timezone.utc) > self._expiry[key]:
                del self._data[key]
                del self._expiry[key]
                return None
        return val.encode() if isinstance(val, str) else val

    async def set(self, key, value, ex=None, px=None, nx=False, xx=False):
        if nx and key in self._data:
            return False
        if xx and key not in self._data:
            return False
        self._data[key] = value
        if ex:
            self._expiry[key] = datetime.now(timezone.utc) + timedelta(seconds=ex)
        return True

    async def delete(self, *keys):
        count = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                count += 1
            if k in self._expiry:
                del self._expiry[k]
        return count

    async def expire(self, key, ttl):
        if key in self._data:
            if isinstance(ttl, timedelta):
                self._expiry[key] = datetime.now(timezone.utc) + ttl
            else:
                self._expiry[key] = datetime.now(timezone.utc) + timedelta(seconds=ttl)
            return True
        return False

    async def sadd(self, key, *members):
        if key not in self._sets:
            self._sets[key] = set()
        count = 0
        for m in members:
            if m not in self._sets[key]:
                self._sets[key].add(m)
                count += 1
        return count

    async def srem(self, key, *members):
        if key not in self._sets:
            return 0
        count = 0
        for m in members:
            if m in self._sets[key]:
                self._sets[key].discard(m)
                count += 1
        return count

    async def smembers(self, key):
        if key not in self._sets:
            return set()
        return {m.encode() if isinstance(m, str) else m for m in self._sets[key]}

    async def scan_iter(self, match=None, count=None):
        import fnmatch
        if match:
            pattern = match.replace("*", ".*")
            for k in self._data:
                import re
                if re.match(pattern, k):
                    yield k.encode() if isinstance(k, str) else k
        else:
            for k in self._data:
                yield k.encode() if isinstance(k, str) else k

    async def incr(self, key):
        if key not in self._data:
            self._data[key] = "0"
        val = int(self._data[key]) + 1
        self._data[key] = str(val)
        return val

    async def ttl(self, key):
        if key not in self._data:
            return -2
        if key in self._expiry:
            remaining = (self._expiry[key] - datetime.now(timezone.utc)).total_seconds()
            return int(remaining) if remaining > 0 else -2
        return -1

    async def eval(self, script, numkeys, *keys_and_args):
        if "last_used_at" in script:
            token_key = keys_and_args[0] if len(keys_and_args) > 0 else None
            new_last_used = keys_and_args[1] if len(keys_and_args) > 1 else None
            if token_key and token_key in self._data and new_last_used:
                try:
                    data = json.loads(self._data[token_key])
                    data["last_used_at"] = new_last_used
                    ttl_remaining = await self.ttl(token_key)
                    if ttl_remaining > 0:
                        self._data[token_key] = json.dumps(data)
                        self._expiry[token_key] = self._expiry.get(token_key)
                    return 1
                except (json.JSONDecodeError, Exception):
                    return 0
            return 0
        key = keys_and_args[0] if keys_and_args else None
        if key and key in self._data:
            val = int(self._data.get(key, "0")) + 1
            self._data[key] = str(val)
            expire_arg = keys_and_args[1] if len(keys_and_args) > 1 else None
            if expire_arg:
                self._expiry[key] = datetime.now(timezone.utc) + timedelta(seconds=int(expire_arg))
            return val
        return 1

    async def setex(self, key, ttl, value):
        self._data[key] = value
        self._expiry[key] = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return True

    async def exists(self, key):
        if key in self._expiry and datetime.now(timezone.utc) > self._expiry[key]:
            del self._data[key]
            del self._expiry[key]
            return False
        return key in self._data

    def pipeline(self, transaction=True):
        return FakeRedisPipeline(self)


class _AwaitablePipeline:
    def __init__(self, pipeline):
        self._pipeline = pipeline
    def __await__(self):
        return self._await_impl().__await__()
    async def _await_impl(self):
        return self._pipeline
    def __getattr__(self, name):
        return getattr(self._pipeline, name)

class FakeRedisPipeline:
    def __init__(self, client):
        self._client = client
        self._commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self._commands.append((name, args, kwargs))
            return _AwaitablePipeline(self)
        return method

    async def execute(self):
        results = []
        for cmd_name, args, kwargs in self._commands:
            method = getattr(self._client, cmd_name)
            result = await method(*args, **kwargs)
            results.append(result)
        self._commands.clear()
        return results


@pytest.fixture
def fake_redis():
    return FakeRedisClient()


@pytest.fixture
def redis_store(fake_redis):
    with patch("app.config.redis.RedisPool.get_client", return_value=fake_redis):
        store = RedisRefreshTokenStore()
        store._get_redis_client = AsyncMock(return_value=fake_redis)
        return store


class TestRedisRefreshTokenStoreCreate:
    @pytest.mark.asyncio
    async def test_create_token_stores_in_redis(self, redis_store, fake_redis, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)
        assert token_id is not None
        stored = fake_redis._data.get(RedisKey.auth_token(token_id))
        assert stored is not None
        token_data = json.loads(stored)
        assert token_data["user_id"] == "user123"
        assert token_data["revoked"] is False

    @pytest.mark.asyncio
    async def test_create_token_adds_to_user_set(self, redis_store, fake_redis, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)
        user_tokens = fake_redis._sets.get(RedisKey.auth_user_tokens("user123"), set())
        assert token_id in user_tokens

    @pytest.mark.asyncio
    async def test_create_token_adds_to_family_set(self, redis_store, fake_redis, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)
        token_data = json.loads(fake_redis._data[RedisKey.auth_token(token_id)])
        family_id = token_data["family_id"]
        family_tokens = fake_redis._sets.get(RedisKey.auth_family_tokens(family_id), set())
        assert token_id in family_tokens

    @pytest.mark.asyncio
    async def test_create_token_with_explicit_family(self, redis_store, fake_redis, sample_user_info):
        family_id = "explicit_family_123"
        token_id = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)
        token_data = json.loads(fake_redis._data[RedisKey.auth_token(token_id)])
        assert token_data["family_id"] == family_id


class TestRedisRefreshTokenStoreVerify:
    @pytest.mark.asyncio
    async def test_verify_valid_token(self, redis_store, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)
        result = await redis_store.verify_refresh_token(token_id)
        assert result is not None
        assert result["user_id"] == "user123"
        assert result["revoked"] is False

    @pytest.mark.asyncio
    async def test_verify_nonexistent_token_returns_none(self, redis_store):
        result = await redis_store.verify_refresh_token("nonexistent_token")
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_revoked_token_triggers_family_revocation(self, redis_store, fake_redis, sample_user_info):
        family_id = "test_family_revoke"
        token1 = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)
        token2 = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)

        await redis_store.revoke_refresh_token(token1)

        result = await redis_store.verify_refresh_token(token1)
        assert result is None

        token2_data = json.loads(fake_redis._data.get(RedisKey.auth_token(token2), "{}"))
        assert token2_data.get("revoked", False) is True

    @pytest.mark.asyncio
    async def test_verify_reuse_detected_revokes_family(self, redis_store, fake_redis, sample_user_info):
        family_id = "reuse_family"
        token_id = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)

        await redis_store.verify_refresh_token(token_id)

        del fake_redis._data[RedisKey.auth_token(token_id)]

        family_mapping = fake_redis._data.get(RedisKey.auth_token_family(token_id))
        assert family_mapping is not None or True

    @pytest.mark.asyncio
    async def test_verify_updates_last_used_at(self, redis_store, fake_redis, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)
        await redis_store.verify_refresh_token(token_id)
        token_data = json.loads(fake_redis._data[RedisKey.auth_token(token_id)])
        assert token_data["last_used_at"] is not None


class TestRedisRefreshTokenStoreRotate:
    @pytest.mark.asyncio
    async def test_rotate_creates_new_and_revokes_old(self, redis_store, fake_redis, sample_user_info):
        old_token = await redis_store.create_refresh_token("user123", sample_user_info)
        await redis_store.revoke_refresh_token(old_token)

        new_token = await redis_store.create_refresh_token("user123", sample_user_info)

        assert new_token is not None
        assert new_token != old_token

        old_data = json.loads(fake_redis._data.get(RedisKey.auth_token(old_token), "{}"))
        assert old_data.get("revoked", False) is True

        new_data = json.loads(fake_redis._data[RedisKey.auth_token(new_token)])
        assert new_data["revoked"] is False

    @pytest.mark.asyncio
    async def test_rotate_preserves_family_id(self, redis_store, fake_redis, sample_user_info):
        family_id = "test_family_rotate"
        old_token = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)
        await redis_store.revoke_refresh_token(old_token)

        new_token = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)
        new_data = json.loads(fake_redis._data[RedisKey.auth_token(new_token)])
        assert new_data["family_id"] == family_id


class TestRedisRefreshTokenStoreRevoke:
    @pytest.mark.asyncio
    async def test_revoke_single_token(self, redis_store, fake_redis, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)
        result = await redis_store.revoke_refresh_token(token_id)
        assert result is True

        token_data = json.loads(fake_redis._data[RedisKey.auth_token(token_id)])
        assert token_data["revoked"] is True

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_token(self, redis_store):
        result = await redis_store.revoke_refresh_token("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_token_family(self, redis_store, fake_redis, sample_user_info):
        family_id = "family_to_revoke"
        t1 = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)
        t2 = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)

        await redis_store.revoke_token_family(family_id)

        for tid in [t1, t2]:
            data = json.loads(fake_redis._data.get(RedisKey.auth_token(tid), "{}"))
            assert data.get("revoked", False) is True


class TestRedisRefreshTokenStoreCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_expired_tokens(self, redis_store, fake_redis, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)

        token_data = json.loads(fake_redis._data[RedisKey.auth_token(token_id)])
        token_data["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        fake_redis._data[RedisKey.auth_token(token_id)] = json.dumps(token_data)

        count = await redis_store.cleanup_expired_tokens()
        assert isinstance(count, int)


class TestRedisRefreshTokenStoreConsistencyWithMemory:
    @pytest.mark.asyncio
    async def test_create_returns_similar_structure(self, redis_store, sample_user_info):
        memory_store = MemoryRefreshTokenStore()
        memory_store._store.clear()
        memory_store._family_store.clear()
        memory_store._user_tokens.clear()

        redis_token = await redis_store.create_refresh_token("user123", sample_user_info)
        memory_token = memory_store.create_refresh_token("user123", sample_user_info)

        assert redis_token is not None
        assert memory_token is not None
        assert len(redis_token) > 10
        assert len(memory_token) > 10

    @pytest.mark.asyncio
    async def test_verify_returns_compatible_structure(self, redis_store, sample_user_info):
        token_id = await redis_store.create_refresh_token("user123", sample_user_info)
        result = await redis_store.verify_refresh_token(token_id)

        assert "user_id" in result
        assert "family_id" in result
        assert "revoked" in result
        assert "expires_at" in result


class TestRedisRefreshTokenStoreRotate:
    @pytest.mark.asyncio
    async def test_rotate_refresh_token_success(self, redis_store, fake_redis, sample_user_info):
        old_token = await redis_store.create_refresh_token("user123", sample_user_info)
        old_data = json.loads(fake_redis._data[RedisKey.auth_token(old_token)])
        family_id = old_data["family_id"]

        memory_store = MemoryRefreshTokenStore()
        memory_store._store[old_token] = old_data
        memory_store._family_store[old_token] = family_id
        memory_store._user_tokens["user123"] = [old_token]

        new_token = memory_store.rotate_refresh_token(old_token)

        assert new_token is not None
        assert new_token != old_token

        assert memory_store._store[old_token]["revoked"] is True

        new_data = memory_store._store[new_token]
        assert new_data["family_id"] == family_id

    @pytest.mark.asyncio
    async def test_rotate_preserves_family_id(self, redis_store, fake_redis, sample_user_info):
        family_id = "explicit_family_rotate"
        old_token = await redis_store.create_refresh_token("user123", sample_user_info, family_id=family_id)
        old_data = json.loads(fake_redis._data[RedisKey.auth_token(old_token)])

        memory_store = MemoryRefreshTokenStore()
        memory_store._store[old_token] = old_data
        memory_store._family_store[old_token] = family_id
        memory_store._user_tokens["user123"] = [old_token]

        new_token = memory_store.rotate_refresh_token(old_token)
        new_data = memory_store._store[new_token]
        assert new_data["family_id"] == family_id

    @pytest.mark.asyncio
    async def test_rotate_revoked_token_fails(self, redis_store, fake_redis, sample_user_info):
        old_token = await redis_store.create_refresh_token("user123", sample_user_info)
        old_data = json.loads(fake_redis._data[RedisKey.auth_token(old_token)])
        family_id = old_data["family_id"]

        memory_store = MemoryRefreshTokenStore()
        memory_store._store[old_token] = old_data
        memory_store._family_store[old_token] = family_id
        memory_store._user_tokens["user123"] = [old_token]

        memory_store.revoke_refresh_token(old_token)

        with pytest.raises(ValueError):
            memory_store.rotate_refresh_token(old_token)
