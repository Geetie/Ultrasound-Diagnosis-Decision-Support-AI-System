"""撤销性能基准测试


验证Token撤销和同意撤回的性能指标：
- 单Token撤销 P99 < 100ms
- 家族撤销 P99 < 500ms
- 同意撤回传播 P99 < 500ms
- Redis RefreshTokenStore 单Token撤销 P99 < 200ms
- Redis RefreshTokenStore 家族撤销 P99 < 500ms
"""

import time
import gc
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf]

from app.config.redis_keys import RedisKey
from app.security.refresh_token import (
    MemoryRefreshTokenStore,
    create_refresh_token,
    revoke_refresh_token,
    revoke_token_family,
    verify_refresh_token,
)


def compute_percentile(sorted_data: list[float], p: int) -> float:
    n = len(sorted_data)
    if n == 0:
        return 0.0
    index = (p / 100) * (n - 1)
    if index.is_integer():
        return sorted_data[int(index)]
    lower = sorted_data[int(index)]
    upper = sorted_data[min(int(index) + 1, n - 1)]
    return lower + (upper - lower) * (index - int(index))


class TestRevocationPerformance:
    """撤销操作性能基准测试"""

    def setup_method(self):
        MemoryRefreshTokenStore._instance = None
        self.store = MemoryRefreshTokenStore()

    @pytest.mark.asyncio
    async def test_single_token_revocation_p99(self):
        """单Token撤销P99 < 100ms"""
        user_id = "perf_user"
        user_info = {"role": "test"}

        for _ in range(50):
            token_id = self.store.create_refresh_token(user_id, user_info)
            self.store.revoke_refresh_token(token_id)

        token_ids = []
        for _ in range(1000):
            token_id = self.store.create_refresh_token(user_id, user_info)
            token_ids.append(token_id)

        gc.disable()
        try:
            latencies = []
            for token_id in token_ids:
                start = time.time()
                self.store.revoke_refresh_token(token_id)
                elapsed = (time.time() - start) * 1000
                latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)

        print(f"\n单Token撤销延迟统计 (n={len(latencies)}):")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")
        print(f"  P99: {p99:.3f}ms")
        print(f"  平均: {sum(latencies)/len(latencies):.3f}ms")

        assert p99 < 100.0, f"单Token撤销P99延迟{p99:.3f}ms超过100ms阈值"

    @pytest.mark.asyncio
    async def test_family_revocation_p99(self):
        """家族撤销P99 < 500ms"""
        user_id = "perf_user"
        user_info = {"role": "test"}

        for _ in range(20):
            MemoryRefreshTokenStore._instance = None
            warmup_store = MemoryRefreshTokenStore()
            warmup_family = "warmup_family"
            for _ in range(10):
                warmup_store.create_refresh_token(user_id, user_info, family_id=warmup_family)
            warmup_store.revoke_token_family(warmup_family)

        family_sizes = [5, 10, 20, 50]

        gc.disable()
        try:
            latencies = []
            for size in family_sizes:
                for _ in range(100):
                    MemoryRefreshTokenStore._instance = None
                    store = MemoryRefreshTokenStore()
                    family_id = f"family_{size}"
                    for _ in range(size):
                        store.create_refresh_token(user_id, user_info, family_id=family_id)

                    start = time.time()
                    store.revoke_token_family(family_id)
                    elapsed = (time.time() - start) * 1000
                    latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)

        print(f"\n家族撤销延迟统计 (n={len(latencies)}):")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")
        print(f"  P99: {p99:.3f}ms")
        print(f"  平均: {sum(latencies)/len(latencies):.3f}ms")

        assert p99 < 500.0, f"家族撤销P99延迟{p99:.3f}ms超过500ms阈值"

    @pytest.mark.asyncio
    async def test_token_reuse_detection_performance(self):
        """Token重用检测+家族撤销P99 < 500ms"""
        user_id = "perf_user"
        user_info = {"role": "test"}

        gc.disable()
        try:
            latencies = []
            for _ in range(500):
                MemoryRefreshTokenStore._instance = None
                store = MemoryRefreshTokenStore()
                family_id = f"family_reuse"
                token1 = store.create_refresh_token(user_id, user_info, family_id=family_id)
                store.create_refresh_token(user_id, user_info, family_id=family_id)
                store.revoke_refresh_token(token1)

                start = time.time()
                store.verify_refresh_token(token1)
                elapsed = (time.time() - start) * 1000
                latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)

        print(f"\nToken重用检测延迟统计 (n={len(latencies)}):")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")
        print(f"  P99: {p99:.3f}ms")

        assert p99 < 500.0, f"Token重用检测P99延迟{p99:.3f}ms超过500ms阈值"


class TestHSMPerformance:
    """HSM签名性能基准测试"""

    def setup_method(self):
        from app.security.hsm import HSMManager
        self.hsm = HSMManager()
        self.hsm.generate_key_pair("perf_test_key")

    def test_sign_p99_latency(self):
        """RSA签名P99 < 5ms"""
        test_data = b"test data for signing performance benchmark"

        for _ in range(50):
            self.hsm.sign("perf_test_key", test_data)

        gc.disable()
        try:
            latencies = []
            for _ in range(1000):
                start = time.time()
                self.hsm.sign("perf_test_key", test_data)
                elapsed = (time.time() - start) * 1000
                latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)

        print(f"\nRSA签名延迟统计 (n={len(latencies)}):")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")
        print(f"  P99: {p99:.3f}ms")
        print(f"  平均: {sum(latencies)/len(latencies):.3f}ms")

        assert p99 < 5.0, f"签名P99延迟{p99:.3f}ms超过5ms阈值"

    def test_verify_p99_latency(self):
        """RSA验证P99 < 5ms"""
        test_data = b"test data for verification performance benchmark"
        signature = self.hsm.sign("perf_test_key", test_data)

        gc.disable()
        try:
            latencies = []
            for _ in range(1000):
                start = time.time()
                self.hsm.verify_signature("perf_test_key", test_data, signature)
                elapsed = (time.time() - start) * 1000
                latencies.append(elapsed)
        finally:
            gc.enable()

        latencies.sort()
        p50 = compute_percentile(latencies, 50)
        p95 = compute_percentile(latencies, 95)
        p99 = compute_percentile(latencies, 99)

        print(f"\nRSA验证延迟统计 (n={len(latencies)}):")
        print(f"  P50: {p50:.3f}ms")
        print(f"  P95: {p95:.3f}ms")
        print(f"  P99: {p99:.3f}ms")

        assert p99 < 5.0, f"验证P99延迟{p99:.3f}ms超过5ms阈值"


class TestRedisRevocationPerformance:
    """Redis RefreshTokenStore撤销性能基准测试

    生产环境使用RedisRefreshTokenStore，需要验证Redis交互下的性能。
    使用Mock Redis模拟Redis延迟（1-3ms网络延迟）。
    """

    def _create_mock_redis_store(self, latency_ms: float = 2.0):
        from app.security.refresh_token import RedisRefreshTokenStore

        mock_redis = AsyncMock()
        _store = {}
        _sets = {}

        async def mock_get(key):
            await self._simulate_latency(latency_ms)
            return _store.get(key)

        async def mock_set(key, value, **kwargs):
            await self._simulate_latency(latency_ms)
            _store[key] = value

        async def mock_delete(key):
            await self._simulate_latency(latency_ms)
            _store.pop(key, None)

        async def mock_sadd(key, *members):
            await self._simulate_latency(latency_ms)
            if key not in _sets:
                _sets[key] = set()
            _sets[key].update(members)

        async def mock_srem(key, *members):
            await self._simulate_latency(latency_ms)
            if key in _sets:
                _sets[key].difference_update(members)

        async def mock_smembers(key):
            await self._simulate_latency(latency_ms)
            return _sets.get(key, set())

        async def mock_exists(key):
            await self._simulate_latency(latency_ms)
            return key in _store

        mock_redis.get = mock_get
        mock_redis.set = mock_set
        mock_redis.delete = mock_delete
        mock_redis.sadd = mock_sadd
        mock_redis.srem = mock_srem
        mock_redis.smembers = mock_smembers
        mock_redis.exists = mock_exists

        class MockPipeline:
            def __init__(self):
                self.ops = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def set(self, key, value, **kwargs):
                self.ops.append(('set', key, value))

            async def srem(self, key, *members):
                self.ops.append(('srem', key, members))

            async def sadd(self, key, *members):
                self.ops.append(('sadd', key, members))

            async def execute(self):
                for op in self.ops:
                    if op[0] == 'set':
                        _store[op[1]] = op[2]
                    elif op[0] == 'srem':
                        if op[1] in _sets:
                            _sets[op[1]].difference_update(op[2])
                    elif op[0] == 'sadd':
                        if op[1] not in _sets:
                            _sets[op[1]] = set()
                        _sets[op[1]].update(op[2])
                self.ops.clear()

        def mock_pipeline(transaction=False):
            return MockPipeline()

        mock_redis.pipeline = mock_pipeline

        return mock_redis, _store, _sets

    async def _simulate_latency(self, ms: float):
        import asyncio
        await asyncio.sleep(ms / 1000.0)

    @pytest.mark.asyncio
    async def test_redis_single_token_revocation_p99(self):
        """Redis单Token撤销P99 < 200ms"""
        from app.security.refresh_token import RedisRefreshTokenStore

        mock_redis, _store, _sets = self._create_mock_redis_store(latency_ms=1.0)

        with patch('app.config.redis.RedisPool') as mock_pool:
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            store = RedisRefreshTokenStore()
            store._redis = mock_redis

            for i in range(20):
                warmup_key = RedisKey.auth_token(f"warmup_{i}")
                import json
                _store[warmup_key] = json.dumps({"user_id": "warmup", "revoked": False, "family_id": f"warmup_fam_{i}"})
                _sets.setdefault(RedisKey.auth_user_tokens("warmup"), set()).add(f"warmup_{i}")
                _sets.setdefault(RedisKey.auth_family_tokens(f"warmup_fam_{i}"), set()).add(f"warmup_{i}")
                await store.revoke_refresh_token(f"warmup_{i}")

            user_id = "perf_user"
            user_info = {"role": "test"}
            token_ids = []
            for i in range(200):
                token_id = f"token_{i}"
                import json
                _store[RedisKey.auth_token(token_id)] = json.dumps({
                    "user_id": user_id,
                    "revoked": False,
                    "family_id": f"family_{i}",
                    "user_info": user_info,
                })
                _sets.setdefault(RedisKey.auth_user_tokens(user_id), set()).add(token_id)
                _sets.setdefault(RedisKey.auth_family_tokens(f"family_{i}"), set()).add(token_id)
                token_ids.append(token_id)

            gc.disable()
            try:
                latencies = []
                for token_id in token_ids:
                    start = time.perf_counter()
                    await store.revoke_refresh_token(token_id)
                    elapsed = (time.perf_counter() - start) * 1000
                    latencies.append(elapsed)
            finally:
                gc.enable()

        latencies.sort()
        p99 = compute_percentile(latencies, 99)

        print(f"\nRedis单Token撤销延迟统计 (n={len(latencies)}):")
        print(f"  P99: {p99:.3f}ms")

        assert p99 < 200.0, f"Redis单Token撤销P99延迟{p99:.3f}ms超过200ms阈值"

    @pytest.mark.asyncio
    async def test_redis_family_revocation_p99(self):
        """Redis家族撤销P99 < 500ms"""
        from app.security.refresh_token import RedisRefreshTokenStore

        mock_redis, _store, _sets = self._create_mock_redis_store(latency_ms=1.0)

        with patch('app.config.redis.RedisPool') as mock_pool:
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            store = RedisRefreshTokenStore()
            store._redis = mock_redis

            for _ in range(10):
                warmup_family = f"warmup_family_{_}"
                for i in range(5):
                    warmup_token = f"warmup_t_{_}_{i}"
                    import json
                    _store[RedisKey.auth_token(warmup_token)] = json.dumps({"user_id": "warmup", "revoked": False, "family_id": warmup_family})
                    _sets.setdefault(RedisKey.auth_user_tokens("warmup"), set()).add(warmup_token)
                    _sets.setdefault(RedisKey.auth_family_tokens(warmup_family), set()).add(warmup_token)
                await store.revoke_token_family(warmup_family)

            family_sizes = [5, 10, 20]

            gc.disable()
            try:
                latencies = []
                for size in family_sizes:
                    for trial in range(50):
                        family_id = f"family_{size}_{trial}"
                        for i in range(size):
                            token_id = f"token_{size}_{trial}_{i}"
                            import json
                            _store[RedisKey.auth_token(token_id)] = json.dumps({
                                "user_id": "user",
                                "revoked": False,
                                "family_id": family_id,
                            })
                            _sets.setdefault(RedisKey.auth_user_tokens("user"), set()).add(token_id)
                            _sets.setdefault(RedisKey.auth_family_tokens(family_id), set()).add(token_id)

                        start = time.perf_counter()
                        await store.revoke_token_family(family_id)
                        elapsed = (time.perf_counter() - start) * 1000
                        latencies.append(elapsed)
            finally:
                gc.enable()

        latencies.sort()
        p99 = compute_percentile(latencies, 99)

        print(f"\nRedis家族撤销延迟统计 (n={len(latencies)}):")
        print(f"  P99: {p99:.3f}ms")

        assert p99 < 500.0, f"Redis家族撤销P99延迟{p99:.3f}ms超过500ms阈值"
