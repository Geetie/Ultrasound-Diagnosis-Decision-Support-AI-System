"""守卫：基础设施巡检的连接串必须来自 settings，且 asyncpg 参数类型必须正确。

## 为什么需要它（2026-09-28 真机部署暴露）

1. `run_infra_health_checks(redis_url="redis://localhost:6379", pg_dsn="postgresql://localhost:5432/ultrasound")`
   把连接串**写死在签名里**，而 lifespan 是无参调用 → 真机上巡检一直在探测两个
   **不存在的服务**，日志被健康检查错误刷满（真机 Redis 在 16379 + requirepass、
   PG 在 15432）。
2. `check_pg_health` 把 `f"{n} seconds"` 当 `$1::interval` 传给 asyncpg ——
   asyncpg 要的是 `datetime.timedelta`，于是每次都抛
   `'str' object has no attribute 'days'`（真机上这条被测不到，因为连接本身先失败了）。
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import settings
from app.observability import infra_health


class TestDefaultsComeFromSettings:
    def test_签名默认值为None而非写死地址(self):
        params = inspect.signature(infra_health.run_infra_health_checks).parameters

        assert params["redis_url"].default is None
        assert params["pg_dsn"].default is None

    def test_巡检使用settings里的连接串(self):
        captured: dict[str, str] = {}

        async def _fake_redis(redis_url):
            captured["redis_url"] = redis_url

        async def _fake_pg(pg_dsn):
            captured["pg_dsn"] = pg_dsn

        async def _run_and_stop():
            infra_health._infra_shutdown_event = None
            task = asyncio.create_task(
                infra_health.run_infra_health_checks(interval_seconds=0.01)
            )
            for _ in range(200):
                if len(captured) >= 2:
                    break
                await asyncio.sleep(0.01)
            await infra_health.stop_infra_health_checks()
            await asyncio.wait_for(task, timeout=2.0)

        with (
            patch.object(infra_health, "check_redis_health", _fake_redis),
            patch.object(infra_health, "check_pg_health", _fake_pg),
        ):
            asyncio.run(_run_and_stop())

        assert captured["redis_url"] == settings.redis_url
        assert captured["pg_dsn"] == settings.database_url.replace("+asyncpg", "")


class TestPgIntervalParameter:
    @pytest.mark.asyncio
    async def test_interval参数必须是timedelta(self):
        captured: dict = {}

        class _FakeConn:
            async def fetchval(self, query, *args):
                captured["args"] = args
                return 0

        class _FakeAcquire:
            async def __aenter__(self):
                return _FakeConn()

            async def __aexit__(self, *exc):
                return False

        fake_pool = MagicMock()
        fake_pool.acquire = MagicMock(return_value=_FakeAcquire())
        fake_pool.get_idle_size = MagicMock(return_value=1)
        fake_pool.get_max_size = MagicMock(return_value=10)
        fake_pool.close = AsyncMock()

        with patch("asyncpg.create_pool", AsyncMock(return_value=fake_pool)):
            await infra_health.check_pg_health("postgresql://localhost/db")

        interval = captured["args"][0]

        assert isinstance(interval, timedelta), (
            "asyncpg 的 interval 参数要 timedelta；传字符串会抛 "
            "'str' object has no attribute 'days'"
        )

    @pytest.mark.asyncio
    async def test_传入SQLAlchemy风格DSN也能连(self):
        """settings.database_url 带 `+asyncpg` 后缀，裸 asyncpg 不认。"""
        seen: dict = {}

        async def _create_pool(dsn, **kwargs):
            seen["dsn"] = dsn
            raise RuntimeError("stop here - 只验证 DSN 形态")

        with patch("asyncpg.create_pool", _create_pool):
            await infra_health.check_pg_health("postgresql+asyncpg://u:p@h:1/d")

        assert "+asyncpg" not in seen["dsn"]
