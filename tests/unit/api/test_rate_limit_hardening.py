"""并发与性能审查**第二批**修复的回归（2026-10-03 晚间）。

覆盖本轮从「未修」转为「已修」的四项：

- **P1-12 登录限流双维度**：原先登录桶键是 ``f"{client_ip}:{user_id}:{path}"``，而登录
  请求没有 token → ``user_id`` 落回 client_ip，于是**同一医院出口 NAT 下的所有医生
  共享一个 5/min 桶**（真机实测：压测准备 10 个账号时被逼退避重试，第 6 个起 429）。
  改为两个正交桶：IP 桶 30/min（挡单主机洪水）+ 用户名桶 5/min（挡撞库）。
- **P2-1 报告链路限流**：``/api/v1/report``（草稿）与带路径参数的
  ``/api/v1/report/*/sign|push``（HSM 签名 / PACS 推送）原先完全无限流；因为中间件
  之前只做**字面量**路径匹配，带路径参数的端点根本无法登记 → 引入前缀解析。
- **P2-8 DLQ 出口**：死信此前只写不读，失败任务被静默丢弃；现在巡检读深度并非零告警。
- **P2-9 多 worker 订阅放大**：``--workers > 1`` 时启动即告警（生产是 1，无影响）。
"""
import pytest

from app.api.v1 import auth as auth_api
from app.api.v1.errors import RateLimitExceededException
from app.config import rate_limit as rl
from app.observability import infra_health

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_buckets():
    rl._buckets.clear()
    yield
    rl._buckets.clear()


class TestLoginTwoDimensionalRateLimit:
    def test_ip_bucket_raised_and_username_bucket_exists(self):
        """IP 桶 30/min（NAT 场景）+ 用户名桶 5/min（撞库场景）两个都要在。"""
        assert rl.RATE_LIMITS[rl.LOGIN_RATE_LIMIT_PATH]["capacity"] == 30
        assert rl.LOGIN_USER_BUCKET_CAPACITY == 5

    async def test_same_username_is_throttled_after_five(self):
        """同一账号连续登录：第 6 次必须被拒（撞库防护仍然有效）。"""
        for _ in range(5):
            await rl.enforce_login_rate_limit("doctor-a")
        with pytest.raises(RateLimitExceededException):
            await rl.enforce_login_rate_limit("doctor-a")

    async def test_different_usernames_do_not_share_the_bucket(self):
        """**P1-12 的核心断言**：同一出口 IP 下不同医生互不影响。"""
        for i in range(12):
            await rl.enforce_login_rate_limit(f"doctor-{i}")

    async def test_same_username_across_ips_is_still_throttled(self):
        """换 IP 撞同一个账号仍被挡（桶键里没有 IP）。"""
        for i in range(12):
            assert await rl.check_rate_limit(
                rl.LOGIN_RATE_LIMIT_PATH, f"10.0.0.{i}", "login:target", capacity_override=5
            ) is None

    async def test_username_is_hashed_in_the_bucket_key(self):
        """桶键里不得出现明文用户名（隐私：日志/Redis 键都不该留 PHI）。"""
        await rl.enforce_login_rate_limit("dr-zhang")
        keys = list(rl._buckets)
        assert keys
        assert all("dr-zhang" not in k for k in keys)
        assert any(k.startswith("*:login:") for k in keys)

    async def test_both_login_endpoints_call_the_guard(self):
        """两个登录端点都必须在 authenticate_user 之前限流（否则白付 bcrypt 的 CPU）。"""
        import inspect

        for func in (auth_api.login, auth_api.login_for_access_token):
            src = inspect.getsource(func)
            assert "await enforce_login_rate_limit(" in src, func.__name__
            # 比对**调用**而不是裸名字：注释里也提到 authenticate_user
            assert src.index("await enforce_login_rate_limit(") < src.index(
                "await authenticate_user("
            ), func.__name__


class TestReportPathRateLimit:
    def test_report_paths_registered(self):
        assert "/api/v1/report" in rl.RATE_LIMITS
        assert "/api/v1/report/*/sign" in rl.RATE_LIMITS
        assert "/api/v1/report/*/push" in rl.RATE_LIMITS

    def test_exact_path_resolution(self):
        assert rl.resolve_rate_limit_config("/api/v1/report")[0] == "/api/v1/report"

    def test_parameterized_path_resolution(self):
        """带路径参数的真实请求路径必须能命中登记（中间件拿不到模板）。"""
        assert (
            rl.resolve_rate_limit_config("/api/v1/report/rpt_abc123/sign")[0]
            == "/api/v1/report/*/sign"
        )
        assert (
            rl.resolve_rate_limit_config("/api/v1/report/rpt_abc123/push")[0]
            == "/api/v1/report/*/push"
        )

    def test_unregistered_path_returns_none(self):
        assert rl.resolve_rate_limit_config("/api/v1/sessions") is None

    def test_prefix_does_not_over_match(self):
        """`/api/v1/report/*/sign` 不能把 `/api/v1/reportx/...` 也捞进来。"""
        assert rl.resolve_rate_limit_config("/api/v1/report-archive/1/sign") is None

    async def test_parameterized_path_is_actually_throttled(self):
        for _ in range(10):
            assert (
                await rl.check_rate_limit("/api/v1/report/*/sign", "1.2.3.4", "u1") is None
            )
        assert await rl.check_rate_limit("/api/v1/report/*/sign", "1.2.3.4", "u1") is not None


class TestDlqOutlet:
    async def test_depth_is_reported_and_warned(self, monkeypatch):
        """非零深度必须能被巡检发现（此前 DLQ 只写不读）。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        client = MagicMock()
        client.llen = AsyncMock(return_value=3)
        with patch(
            "app.config.redis.RedisPool.get_client", AsyncMock(return_value=client)
        ):
            assert await infra_health.check_dlq_depth() == 3

    async def test_zero_depth_is_quiet(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        client = MagicMock()
        client.llen = AsyncMock(return_value=0)
        with patch(
            "app.config.redis.RedisPool.get_client", AsyncMock(return_value=client)
        ):
            assert await infra_health.check_dlq_depth() == 0

    async def test_redis_failure_returns_minus_one(self):
        """Redis 挂了不能让巡检循环崩（返回 -1 供上层判读）。"""
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.config.redis.RedisPool.get_client",
            AsyncMock(side_effect=ConnectionError("down")),
        ):
            assert await infra_health.check_dlq_depth() == -1

    async def test_uses_the_dlq_key(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.config.redis_keys import RedisKey

        client = MagicMock()
        client.llen = AsyncMock(return_value=1)
        with patch(
            "app.config.redis.RedisPool.get_client", AsyncMock(return_value=client)
        ):
            await infra_health.check_dlq_depth()
        assert client.llen.await_args.args[0] == RedisKey.DIAGNOSIS_DLQ


class TestMultiWorkerWarning:
    """P2-9：``--workers > 1`` 时启动即告警（生产是 1，必须保持安静）。"""

    def test_detects_worker_count_from_env(self, monkeypatch):
        from app.main import _detect_worker_count

        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        assert _detect_worker_count() == 4

    def test_defaults_to_single_worker(self, monkeypatch):
        from app.main import _detect_worker_count

        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        assert _detect_worker_count() == 1

    def test_garbage_value_does_not_raise(self, monkeypatch):
        """环境变量是运维手填的：空串/非数字必须按 1 处理，绝不能让主服务起不来。"""
        from app.main import _detect_worker_count

        for value in ("", "not-a-number", "2.5"):
            monkeypatch.setenv("WEB_CONCURRENCY", value)
            assert _detect_worker_count() == 1

    def test_lifespan_keeps_the_guard(self):
        """告警必须真的挂在 lifespan 启动路径上（否则只是个没人调的函数）。"""
        import inspect

        from app.main import lifespan

        src = inspect.getsource(lifespan)
        assert "multi_worker_nav_redis_subscription_amplified" in src
        assert "_detect_worker_count()" in src
