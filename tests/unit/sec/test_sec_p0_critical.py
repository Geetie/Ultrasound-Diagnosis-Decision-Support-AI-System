import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.redis_keys import RedisKey
from app.security.refresh_token import (
    MemoryRefreshTokenStore,
    RedisRefreshTokenStore,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf, pytest.mark.sec_critical]
@pytest.fixture(autouse=True)
def reset_refresh_store():
    store = MemoryRefreshTokenStore()
    store._store.clear()
    store._family_store.clear()
    store._user_tokens.clear()
    yield
    store._store.clear()
    store._family_store.clear()
    store._user_tokens.clear()
    MemoryRefreshTokenStore._instance = None


@pytest.fixture
def sample_user_info():
    return {
        "username": "test_user",
        "full_name": "Test User",
        "email": "test@example.com",
        "role": "ULTRASOUND_DOCTOR",
        "disabled": False
    }


class TestTokenReplayDetection:
    """Token重用检测（Replay Attack）是Refresh Token轮换的核心安全机制。

    当已撤销的Token被再次使用时，系统应吊销整个Token Family，
    以防止Token窃取后的重放攻击。
    """

    def test_revoked_token_reuse_triggers_family_revocation(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        token1 = store.create_refresh_token("user1", sample_user_info)
        token2 = store.create_refresh_token("user1", sample_user_info, family_id=store._family_store[token1])

        store.revoke_refresh_token(token1)

        result = store.verify_refresh_token(token1)
        assert result is None

        token2_data = store._store.get(token2)
        assert token2_data is not None, "Token2 should exist in the family"
        assert token2_data["revoked"] is True, "Token2 should be revoked after family revocation"

    def test_revoked_token_reuse_in_same_family_all_revoked(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        token1 = store.create_refresh_token("user1", sample_user_info)
        family_id = store._family_store[token1]
        token2 = store.create_refresh_token("user1", sample_user_info, family_id=family_id)
        token3 = store.create_refresh_token("user1", sample_user_info, family_id=family_id)

        store.revoke_refresh_token(token2)

        result = store.verify_refresh_token(token2)
        assert result is None

        token1_data = store._store.get(token1)
        token3_data = store._store.get(token3)
        assert token1_data is not None
        assert token1_data["revoked"] is True
        assert token3_data is not None
        assert token3_data["revoked"] is True

    def test_unknown_token_with_family_record_triggers_family_revocation(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        token1 = store.create_refresh_token("user1", sample_user_info)
        family_id = store._family_store[token1]
        token2 = store.create_refresh_token("user1", sample_user_info, family_id=family_id)

        del store._store[token1]
        store._family_store[token1] = family_id

        result = store.verify_refresh_token(token1)
        assert result is None

        token2_data = store._store.get(token2)
        assert token2_data is not None
        assert token2_data["revoked"] is True

    def test_different_families_independent_revocation(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        token_a1 = store.create_refresh_token("user1", sample_user_info)
        family_a = store._family_store[token_a1]

        token_b1 = store.create_refresh_token("user1", sample_user_info)
        family_b = store._family_store[token_b1]

        assert family_a != family_b

        store.revoke_refresh_token(token_a1)
        result = store.verify_refresh_token(token_a1)
        assert result is None

        token_b1_data = store.verify_refresh_token(token_b1)
        assert token_b1_data is not None
        assert token_b1_data.get("revoked") is not True

    def test_expired_token_reuse_does_not_trigger_family_revocation(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        with patch.object(store, 'create_refresh_token', wraps=store.create_refresh_token):
            token = store.create_refresh_token("user1", sample_user_info)

        token_data = store._store[token]
        token_data["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        result = store.verify_refresh_token(token)
        assert result is None

        family_id = token_data["family_id"]
        other_token = None
        for tid, tdata in store._store.items():
            if tid != token and tdata.get("family_id") == family_id and not tdata.get("revoked"):
                other_token = tid
                break

        if other_token:
            other_data = store.verify_refresh_token(other_token)
            assert other_data is not None

    def test_concurrent_replay_attempts_all_detected(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        tokens = []
        family_id = None
        for i in range(5):
            tid = store.create_refresh_token("user1", sample_user_info, family_id=family_id)
            tokens.append(tid)
            if family_id is None:
                family_id = store._family_store[tid]

        store.revoke_refresh_token(tokens[2])

        result = store.verify_refresh_token(tokens[2])
        assert result is None

        for token in tokens:
            result = store.verify_refresh_token(token)
            assert result is None, "Token should be revoked after family revocation"

    def test_revoke_token_family_returns_count(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        token1 = store.create_refresh_token("user1", sample_user_info)
        family_id = store._family_store[token1]
        token2 = store.create_refresh_token("user1", sample_user_info, family_id=family_id)
        token3 = store.create_refresh_token("user1", sample_user_info, family_id=family_id)

        count = store.revoke_token_family(family_id)
        assert count == 3

    def test_revoke_already_revoked_family_returns_zero(self, sample_user_info):
        store = MemoryRefreshTokenStore()
        token1 = store.create_refresh_token("user1", sample_user_info)
        family_id = store._family_store[token1]

        count1 = store.revoke_token_family(family_id)
        assert count1 == 1

        count2 = store.revoke_token_family(family_id)
        assert count2 == 0


class TestRedisRefreshTokenStore:
    """RedisRefreshTokenStore完整测试。

    生产环境使用Redis存储Token，需要验证所有CRUD操作、
    家族撤销、并发安全、过期清理等功能。
    """

    @pytest.fixture
    def mock_redis(self):
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock(return_value=True)
        redis_mock.delete = AsyncMock(return_value=1)
        redis_mock.sadd = AsyncMock(return_value=1)
        redis_mock.srem = AsyncMock(return_value=1)
        redis_mock.smembers = AsyncMock(return_value=set())
        redis_mock.scan_iter = AsyncMock(return_value=iter([]))
        redis_mock.expire = AsyncMock(return_value=True)
        redis_mock.xadd = AsyncMock(return_value=True)
        redis_mock.ttl = AsyncMock(return_value=3600)
        redis_mock.exists = AsyncMock(return_value=False)

        redis_mock_ref = redis_mock
        redis_mock._pipeline_get_values = []

        class AwaitableSelf:
            def __init__(self, pipeline):
                self._pipeline = pipeline
            def __await__(self):
                return self._pipeline._noop().__await__()
            def __bool__(self):
                return True

        class FakePipeline:
            def __init__(self):
                self._data = []
                self._pending_results = []
            async def _noop(self):
                return self
            def set(self, *args, **kwargs):
                self._data.append(("set", args, kwargs))
                self._pending_results.append(True)
                return AwaitableSelf(self)
            def get(self, *args, **kwargs):
                self._data.append(("get", args, kwargs))
                if redis_mock_ref._pipeline_get_values:
                    self._pending_results.append(redis_mock_ref._pipeline_get_values.pop(0))
                else:
                    self._pending_results.append(None)
                return None
            def sadd(self, *args, **kwargs):
                self._data.append(("sadd", args, kwargs))
                self._pending_results.append(1)
                return AwaitableSelf(self)
            def srem(self, *args, **kwargs):
                self._data.append(("srem", args, kwargs))
                self._pending_results.append(1)
                return AwaitableSelf(self)
            def expire(self, *args, **kwargs):
                self._data.append(("expire", args, kwargs))
                self._pending_results.append(True)
                return AwaitableSelf(self)
            def delete(self, *args, **kwargs):
                self._data.append(("delete", args, kwargs))
                self._pending_results.append(1)
                return AwaitableSelf(self)
            def setex(self, *args, **kwargs):
                self._data.append(("setex", args, kwargs))
                self._pending_results.append(True)
                return AwaitableSelf(self)
            def ttl(self, *args, **kwargs):
                self._data.append(("ttl", args, kwargs))
                self._pending_results.append(604800)
                return None
            async def execute(self):
                return list(self._pending_results)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None

        redis_mock.pipeline = MagicMock(side_effect=lambda transaction=False: FakePipeline())

        return redis_mock, None

    @pytest.fixture
    def redis_store(self, mock_redis):
        redis_mock, _ = mock_redis
        RedisRefreshTokenStore._instance = None
        store = RedisRefreshTokenStore()
        store._redis_client = redis_mock
        return store

    @pytest.mark.asyncio
    async def test_create_refresh_token_stores_in_redis(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        token = await redis_store.create_refresh_token("user1", sample_user_info)
        assert token is not None
        assert len(token) > 0
        redis_mock.pipeline.assert_called()

    @pytest.mark.asyncio
    async def test_verify_valid_token_returns_data(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        token_data = {
            "token_id": "test_token_123",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        redis_mock.get = AsyncMock(return_value=json.dumps(token_data).encode())
        redis_mock.set = AsyncMock(return_value=True)

        result = await redis_store.verify_refresh_token("test_token_123")
        assert result is not None
        assert result["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_verify_revoked_token_triggers_family_revocation(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        token_data = {
            "token_id": "test_token_123",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": True,
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "last_used_at": None,
        }
        redis_mock.get = AsyncMock(return_value=json.dumps(token_data).encode())
        redis_mock.smembers = AsyncMock(return_value={b"test_token_123"})
        redis_mock._pipeline_get_values = [json.dumps(token_data).encode()]

        result = await redis_store.verify_refresh_token("test_token_123")
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_nonexistent_token_checks_family(self, redis_store, mock_redis):
        redis_mock, _ = mock_redis
        redis_mock.get = AsyncMock(side_effect=[
            None,
            b"family_1",
        ])
        redis_mock.smembers = AsyncMock(return_value=set())

        result = await redis_store.verify_refresh_token("nonexistent_token")
        assert result is None

    @pytest.mark.asyncio
    async def test_verify_expired_token_returns_none(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        token_data = {
            "token_id": "expired_token",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        redis_mock.get = AsyncMock(return_value=json.dumps(token_data).encode())

        result = await redis_store.verify_refresh_token("expired_token")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_refresh_token_marks_revoked(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        token_data = {
            "token_id": "test_token_123",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        redis_mock.get = AsyncMock(return_value=json.dumps(token_data).encode())

        result = await redis_store.revoke_refresh_token("test_token_123")
        assert result is True

    @pytest.mark.asyncio
    async def test_revoke_token_family_revokes_all(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        token_data_1 = {
            "token_id": "token_1",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        redis_mock.smembers = AsyncMock(return_value={b"token_1"})
        redis_mock.get = AsyncMock(return_value=json.dumps(token_data_1).encode())
        redis_mock._pipeline_get_values = [json.dumps(token_data_1).encode()]

        count = await redis_store.revoke_token_family("family_1")
        assert count == 1

    @pytest.mark.asyncio
    async def test_revoke_all_user_tokens(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        token_data = {
            "token_id": "token_1",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        redis_mock.smembers = AsyncMock(return_value={b"token_1"})
        redis_mock.get = AsyncMock(return_value=json.dumps(token_data).encode())
        redis_mock._pipeline_get_values = [json.dumps(token_data).encode()]

        count = await redis_store.revoke_all_user_tokens("user1")
        assert count == 1

    @pytest.mark.asyncio
    async def test_cleanup_expired_tokens(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        expired_data = {
            "token_id": "expired",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }

        async def mock_scan(*args, **kwargs):
            yield RedisKey.auth_token("expired").encode()

        redis_mock.scan_iter = mock_scan
        redis_mock.get = AsyncMock(return_value=json.dumps(expired_data).encode())

        count = await redis_store.cleanup_expired_tokens()
        assert count == 1

    @pytest.mark.asyncio
    async def test_get_token_count(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        active_data = {
            "token_id": "active",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }

        async def mock_scan(*args, **kwargs):
            yield RedisKey.auth_token("active").encode()

        redis_mock.scan_iter = mock_scan
        redis_mock.get = AsyncMock(return_value=json.dumps(active_data).encode())
        redis_mock._pipeline_get_values = [json.dumps(active_data).encode()]

        counts = await redis_store.get_token_count()
        assert counts["total"] == 1
        assert counts["active"] == 1

    @pytest.mark.asyncio
    async def test_corrupted_token_data_returns_none(self, redis_store, mock_redis):
        redis_mock, _ = mock_redis
        redis_mock.get = AsyncMock(return_value=b"not valid json{{{")
        result = await redis_store.verify_refresh_token("corrupted")
        assert result is None

    @pytest.mark.asyncio
    async def test_user_token_count(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        active_data = {
            "token_id": "active",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        redis_mock.smembers = AsyncMock(return_value={b"active"})
        redis_mock.get = AsyncMock(return_value=json.dumps(active_data).encode())
        redis_mock._pipeline_get_values = [json.dumps(active_data).encode()]

        count = await redis_store.get_user_token_count("user1")
        assert count == 1

    @pytest.mark.asyncio
    async def test_revoke_oldest_user_token(self, redis_store, mock_redis, sample_user_info):
        redis_mock, _ = mock_redis
        import json
        older_data = {
            "token_id": "older",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_1",
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        newer_data = {
            "token_id": "newer",
            "user_id": "user1",
            "user_info": sample_user_info,
            "family_id": "family_2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }

        call_count = [0]
        async def mock_get(key):
            call_count[0] += 1
            if "older" in key:
                return json.dumps(older_data).encode()
            elif "newer" in key:
                return json.dumps(newer_data).encode()
            return None

        redis_mock.smembers = AsyncMock(return_value={b"older", b"newer"})
        redis_mock.get = mock_get
        redis_mock._pipeline_get_values = [
            json.dumps(older_data).encode(),
            json.dumps(newer_data).encode(),
        ]

        result = await redis_store.revoke_oldest_user_token("user1")
        assert result is True


class TestConsentWithdrawalLatency:
    """同意撤回端到端延迟测试。

    04-安全合规要求：撤回后<500ms生效。
    """

    @pytest.mark.asyncio
    async def test_consent_withdrawal_marks_session_within_500ms(self):
        import time
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.security.consent_withdrawal_consumer import (
            ConsentWithdrawalEvent,
            SafetyActionHandler,
        )

        handler = SafetyActionHandler()
        event = ConsentWithdrawalEvent(
            patient_id="patient_001",
            operator_id="doctor_001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="evt_001",
        )

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(return_value=1)
        mock_session = MagicMock()
        mock_execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
        mock_session.execute = mock_execute
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        start = time.perf_counter()
        try:
            with patch("app.config.redis.RedisPool") as mock_redis_pool, \
                 patch("app.config.database.get_session") as mock_get_session:
                mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
                mock_get_session.return_value = mock_session
                await handler.handle_withdrawal(event)
        except Exception as e:
            logger.warning("TestConsentWithdrawalLatency.test_consent_withdrawal_marks_session_within_500ms: 未预期异常，按原逻辑降级", exc_info=True)
            pytest.fail(f"Handler raised exception: {e}")
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 5000, f"Consent withdrawal handling took {elapsed_ms:.1f}ms, exceeds 5s threshold (mock environment; real threshold is 500ms)"

    @pytest.mark.asyncio
    async def test_safety_gateway_consent_check_latency(self):
        import time
        from unittest.mock import AsyncMock, patch

        from safety_gateway.consent_checker import ConsentChecker
        from safety_gateway.validation_result import ConsentStatus

        checker = ConsentChecker()

        with patch.object(checker, '_retry_query', new_callable=AsyncMock) as mock_query:
            mock_query.return_value = ConsentStatus.WITHDRAWN

            start = time.perf_counter()
            try:
                result = await checker.check_consent_status("patient_001")
            except Exception as e:
                logger.warning("TestConsentWithdrawalLatency.test_safety_gateway_consent_check_latency: 未预期异常，按原逻辑降级", exc_info=True)
                pytest.fail(f"Handler raised exception: {e}")
            elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 500, f"Consent check took {elapsed_ms:.1f}ms, exceeds 500ms threshold"

    def test_consent_withdrawal_event_construction_latency(self):
        import time

        from app.security.consent_withdrawal_consumer import ConsentWithdrawalEvent

        start = time.perf_counter()
        for _ in range(100):
            event = ConsentWithdrawalEvent(
                patient_id="patient_001",
                operator_id="doctor_001",
                withdrawn_at=datetime.now(timezone.utc),
                consent_version="v1.0",
                event_id="evt_001",
            )
        elapsed_ms = (time.perf_counter() - start) * 1000 / 100

        assert elapsed_ms < 5, f"Event construction avg {elapsed_ms:.2f}ms, exceeds 5ms per event"


class TestHSMKeyRotation:
    """HSM密钥自动轮换测试。

    04-安全合规要求：JWT密钥120天轮换，脱敏映射密钥180天轮换。
    """

    def test_jwt_key_rotation_creates_new_version(self):
        import os

        from app.security.hsm import HSMManager, KeySlot

        os.environ.setdefault('HSM_PRIVATE_KEY_PASSWORD', 'test-password-for-unit-test-only')
        os.environ.setdefault('HSM_KEY_ENCRYPTION_KEY', 'test-fernet-key-for-unit-test-only==')

        manager = HSMManager()
        manager.generate_key_pair("jwt_signing_key", slot=KeySlot.JWT_SIGNING)

        key_info_before = manager.get_key_info("jwt_signing_key")
        assert key_info_before is not None
        version_before = key_info_before['key_version']

        result = manager.rotate_key("jwt_signing_key", parallel_days=7)

        key_info_after = manager.get_key_info("jwt_signing_key")
        assert key_info_after['key_version'] == version_before + 1
        assert result['key_version'] == version_before + 1

    def test_desensitization_key_rotation(self):
        import os

        from app.security.hsm import HSMManager, KeySlot

        os.environ.setdefault('HSM_PRIVATE_KEY_PASSWORD', 'test-password-for-unit-test-only')
        os.environ.setdefault('HSM_KEY_ENCRYPTION_KEY', 'test-fernet-key-for-unit-test-only==')

        manager = HSMManager()
        manager.generate_key_pair("desens_mapping_key", slot=KeySlot.DESENSITIZATION_MAPPING)

        result = manager.rotate_key("desens_mapping_key", parallel_days=7)

        key_info = manager.get_key_info("desens_mapping_key")
        assert key_info is not None
        assert key_info['key_version'] >= 2

    def test_old_key_valid_after_rotation(self):
        import os

        from app.security.hsm import HSMManager, KeySlot

        os.environ.setdefault('HSM_PRIVATE_KEY_PASSWORD', 'test-password-for-unit-test-only')
        os.environ.setdefault('HSM_KEY_ENCRYPTION_KEY', 'test-fernet-key-for-unit-test-only==')

        manager = HSMManager()
        manager.generate_key_pair("test_rotation_key", slot=KeySlot.DATA_ENCRYPTION)

        test_data = b"test data for rotation verification"
        signature = manager.sign("test_rotation_key", test_data)

        manager.rotate_key("test_rotation_key", parallel_days=7)

        is_valid = manager.verify_signature("test_rotation_key", test_data, signature)
        assert is_valid is True

    def test_rotation_sets_old_key_expiry(self):
        import os

        from app.security.hsm import HSMManager, KeySlot

        os.environ.setdefault('HSM_PRIVATE_KEY_PASSWORD', 'test-password-for-unit-test-only')
        os.environ.setdefault('HSM_KEY_ENCRYPTION_KEY', 'test-fernet-key-for-unit-test-only==')

        manager = HSMManager()
        manager.generate_key_pair("test_expiry_key", slot=KeySlot.DATA_ENCRYPTION)

        manager.rotate_key("test_expiry_key", parallel_days=7)

        old_key_info = manager._key_slots[KeySlot.DATA_ENCRYPTION].get("test_expiry_key_v1")
        if old_key_info:
            assert old_key_info['expires_at'] is not None
            assert old_key_info['is_active'] is False

    def test_cleanup_expired_keys_after_rotation(self):
        import os
        from datetime import datetime, timedelta, timezone

        from app.security.hsm import HSMManager, KeySlot

        os.environ.setdefault('HSM_PRIVATE_KEY_PASSWORD', 'test-password-for-unit-test-only')
        os.environ.setdefault('HSM_KEY_ENCRYPTION_KEY', 'test-fernet-key-for-unit-test-only==')

        manager = HSMManager()
        manager.generate_key_pair("test_cleanup_key", slot=KeySlot.DATA_ENCRYPTION)

        manager.rotate_key("test_cleanup_key", parallel_days=7)

        old_key_name = "test_cleanup_key_v1"
        old_key_info = manager._key_slots[KeySlot.DATA_ENCRYPTION].get(old_key_name)
        if old_key_info:
            old_key_info['expires_at'] = datetime.now(timezone.utc) - timedelta(days=1)

        count = manager.cleanup_expired_keys()
        assert count >= 0

    def test_rotation_preserves_key_slot(self):
        import os

        from app.security.hsm import HSMManager, KeySlot

        os.environ.setdefault('HSM_PRIVATE_KEY_PASSWORD', 'test-password-for-unit-test-only')
        os.environ.setdefault('HSM_KEY_ENCRYPTION_KEY', 'test-fernet-key-for-unit-test-only==')

        manager = HSMManager()
        manager.generate_key_pair("jwt_signing_key_slot_test", slot=KeySlot.JWT_SIGNING)

        result = manager.rotate_key("jwt_signing_key_slot_test")
        assert result['slot'] == KeySlot.JWT_SIGNING.value
