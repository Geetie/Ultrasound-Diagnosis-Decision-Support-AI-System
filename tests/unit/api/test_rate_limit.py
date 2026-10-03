from unittest.mock import patch

import pytest

from app.config.rate_limit import (
    MAX_BUCKETS,
    RATE_LIMITS,
    TokenBucket,
    _buckets,
    _evict_oldest_bucket,
    check_rate_limit,
)


class TestTokenBucket:
    def test_initial_capacity(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.tokens == 10

    def test_consume_token(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.consume() is True
        assert bucket.tokens == 9

    def test_exhaust_bucket(self):
        bucket = TokenBucket(capacity=2, refill_rate=0.01)
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is False

    def test_refill_tokens(self):
        bucket = TokenBucket(capacity=10, refill_rate=100.0)
        bucket.tokens = 0
        bucket.last_refill = 0
        assert bucket.consume() is True


class TestRateLimitConfig:
    def test_diagnosis_rate_limit(self):
        assert "/api/v1/diagnosis/request" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/diagnosis/request"]["capacity"] == 10

    def test_diagnosis_endpoint_managed(self):
        assert RATE_LIMITS["/api/v1/diagnosis/request"].get("endpoint_managed") is True

    def test_imaging_upload_rate_limit(self):
        assert "/api/v1/imaging/dicom/upload" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/imaging/dicom/upload"]["capacity"] == 30

    def test_imaging_stream_rate_limit(self):
        assert "/api/v1/imaging/dicom/stream" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/imaging/dicom/stream"]["capacity"] == 30

    def test_feedback_rate_limit(self):
        assert "/api/v1/feedback" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/feedback"]["capacity"] == 20

    def test_auth_token_rate_limit(self):
        # 并发审查 P1-12：IP 桶 5 -> 30/min。原先登录请求没有 token，user_id 落回
        # client_ip，于是同一医院出口 NAT 下**所有医生共享一个 5/min 桶**（真机实测：
        # 压测准备 10 个账号时被逼退避重试）。撞库防护改由端点内的"用户名桶"承担
        # （见 app/config/rate_limit.py: enforce_login_rate_limit）。
        assert "/api/v1/auth/token" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/auth/token"]["capacity"] == 30

    def test_auth_login_rate_limit(self):
        assert "/api/v1/auth/login" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/auth/login"]["capacity"] == 30

    def test_report_chain_rate_limits(self):
        """并发审查 P2-1：报告链路原先完全无限流；带路径参数的用 `/*` 通配登记。"""
        assert RATE_LIMITS["/api/v1/report"]["capacity"] == 20
        assert RATE_LIMITS["/api/v1/report/*/sign"]["capacity"] == 10
        assert RATE_LIMITS["/api/v1/report/*/push"]["capacity"] == 10

    def test_consent_rate_limit(self):
        assert "/api/v1/consent" in RATE_LIMITS
        assert RATE_LIMITS["/api/v1/consent"]["capacity"] == 10

    def test_fhir_rate_limit(self):
        assert "/fhir" in RATE_LIMITS
        assert RATE_LIMITS["/fhir"]["capacity"] == 60

    def test_all_rate_limits_have_refill_rate(self):
        for path, config in RATE_LIMITS.items():
            assert "capacity" in config, f"Missing capacity for {path}"
            assert "refill_rate" in config, f"Missing refill_rate for {path}"
            assert config["capacity"] > 0, f"Invalid capacity for {path}"
            assert config["refill_rate"] > 0, f"Invalid refill_rate for {path}"


class TestCheckRateLimit:
    @pytest.mark.asyncio
    async def test_normal_request_passes(self):
        _buckets.clear()
        result = await check_rate_limit(
            path="/api/v1/diagnosis/request",
            client_ip="127.0.0.1",
            user_id="user:test",
            is_emergency=False,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_for_retry(self):
        _buckets.clear()
        result = await check_rate_limit(
            path="/api/v1/diagnosis/request",
            client_ip="127.0.0.1",
            user_id="user:test",
            skip=True,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_dedup_key_multi_organ(self):
        _buckets.clear()
        result1 = await check_rate_limit(
            path="/api/v1/diagnosis/request",
            client_ip="127.0.0.1",
            user_id="user:test",
            dedup_key="session_001",
        )
        assert result1 is None

        result2 = await check_rate_limit(
            path="/api/v1/diagnosis/request",
            client_ip="127.0.0.1",
            user_id="user:test",
            dedup_key="session_001",
        )
        assert result2 is None

    @pytest.mark.asyncio
    async def test_dedup_key_different_sessions(self):
        _buckets.clear()
        result1 = await check_rate_limit(
            path="/api/v1/diagnosis/request",
            client_ip="127.0.0.1",
            user_id="user:test",
            dedup_key="session_001",
        )
        assert result1 is None

        result2 = await check_rate_limit(
            path="/api/v1/diagnosis/request",
            client_ip="127.0.0.1",
            user_id="user:test",
            dedup_key="session_002",
        )
        assert result2 is None

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self):
        _buckets.clear()
        for _ in range(10):
            await check_rate_limit(
                path="/api/v1/diagnosis/request",
                client_ip="127.0.0.1",
                user_id="user:test_limited",
            )
        result = await check_rate_limit(
            path="/api/v1/diagnosis/request",
            client_ip="127.0.0.1",
            user_id="user:test_limited",
        )
        assert result is not None
        assert result.error_code == "E1007"


class TestBucketCapacityLimit:
    def test_max_buckets_constant(self):
        assert MAX_BUCKETS == 100000

    def test_evict_oldest_bucket(self):
        _buckets.clear()
        import time
        b1 = TokenBucket(capacity=10, refill_rate=1.0)
        b1.last_access = 0
        _buckets["old_key"] = b1
        b2 = TokenBucket(capacity=10, refill_rate=1.0)
        b2.last_access = time.monotonic()
        _buckets["new_key"] = b2
        _evict_oldest_bucket()
        assert "old_key" not in _buckets
        assert "new_key" in _buckets
        _buckets.clear()

    def test_evict_empty_buckets(self):
        _buckets.clear()
        _evict_oldest_bucket()
        assert len(_buckets) == 0

    @pytest.mark.asyncio
    async def test_bucket_unavailable_returns_none(self):
        _buckets.clear()
        with patch("app.config.rate_limit.settings") as mock_settings:
            mock_settings.rate_limit_redis_enabled = False
            import app.config.rate_limit as rl_module
            original_max = rl_module.MAX_BUCKETS
            rl_module.MAX_BUCKETS = 0
            try:
                result = await check_rate_limit(
                    path="/api/v1/diagnosis/request",
                    client_ip="127.0.0.1",
                    user_id="user:test_full",
                    is_emergency=False,
                )
                assert result is None
            finally:
                rl_module.MAX_BUCKETS = original_max
                _buckets.clear()


class TestDedupKeyPerSession:
    @pytest.mark.asyncio
    async def test_same_session_deduped(self):
        _buckets.clear()
        with patch("app.config.rate_limit.settings") as mock_settings:
            mock_settings.rate_limit_redis_enabled = False
            result1 = await check_rate_limit(
                path="/api/v1/diagnosis/request",
                client_ip="127.0.0.1",
                user_id="user:dedup_session",
                dedup_key="sess_001",
            )
            assert result1 is None

            result2 = await check_rate_limit(
                path="/api/v1/diagnosis/request",
                client_ip="127.0.0.1",
                user_id="user:dedup_session",
                dedup_key="sess_001",
            )
            assert result2 is None
        _buckets.clear()

    @pytest.mark.asyncio
    async def test_different_sessions_both_count(self):
        _buckets.clear()
        with patch("app.config.rate_limit.settings") as mock_settings:
            mock_settings.rate_limit_redis_enabled = False
            result1 = await check_rate_limit(
                path="/api/v1/diagnosis/request",
                client_ip="127.0.0.1",
                user_id="user:dedup_diff",
                dedup_key="sess_003",
            )
            assert result1 is None

            result2 = await check_rate_limit(
                path="/api/v1/diagnosis/request",
                client_ip="127.0.0.1",
                user_id="user:dedup_diff",
                dedup_key="sess_004",
            )
            assert result2 is None
        _buckets.clear()
