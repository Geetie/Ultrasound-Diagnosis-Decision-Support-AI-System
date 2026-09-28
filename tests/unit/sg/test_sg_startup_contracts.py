"""守卫：SafetyGateway 启动期的三项契约（都曾在真机上失效）。

1. **单进程校验**：`_get_actual_worker_count()` 数的是**子进程数**，其文档明确
   "单 worker 时正常就是 0 个"。调用处原先写 `!= 1` → SG 在干净环境下**永远起不来**
   （真机：`sg_workers_must_be_1_actual actual_workers=0` → "Application startup failed"）。
2. **融合层探测路径**：原写 `/health`，而主服务健康路由在 `/api/v1/health`
   → 恒 404 → SG 总判自己 degraded → 主服务又把 degraded 当 unhealthy → 永久 critical。
3. **阈值默认值**：库为空时原实现什么都不灌 → 所有器官都落到
   `sg_default_unknown_organ_threshold`(0.95)，设计里 thyroid 0.85 / liver 0.88 /
   cardiac 0.92 的分级阈值全部失效。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from safety_gateway import threshold as threshold_module
from safety_gateway.health.fusion_probe import FusionProbe
from safety_gateway.main import _assert_single_process
from safety_gateway.sg_validator import SGConfigValidationError
from safety_gateway.threshold import ThresholdEngine


class TestSingleProcessContract:
    @pytest.mark.parametrize("children", [0, None])
    def test_单进程或无判定时放行(self, children):
        _assert_single_process(children)  # 不抛即通过

    @pytest.mark.parametrize("children", [1, 2, 5])
    def test_检测到父进程分叉时拒绝(self, children):
        with pytest.raises(SGConfigValidationError):
            _assert_single_process(children)


class TestFusionProbeUsesRealHealthRoute:
    @pytest.mark.asyncio
    async def test_探测的是主服务的真实健康路由(self):
        probe = FusionProbe()
        captured: dict[str, str] = {}

        class _FakeClient:
            async def get(self, url):
                captured["url"] = url
                return SimpleNamespace(status_code=200)

        probe._ensure_client = lambda: _FakeClient()

        result = await probe.check()

        assert result["status"] == "healthy"
        assert captured["url"].endswith("/api/v1/health"), (
            "主服务的健康路由是 /api/v1/health；探 /health 会恒 404"
        )

    @pytest.mark.asyncio
    async def test_非200记为unhealthy(self):
        probe = FusionProbe()

        class _FakeClient:
            async def get(self, url):
                return SimpleNamespace(status_code=404)

        probe._ensure_client = lambda: _FakeClient()

        result = await probe.check()

        assert result["status"] == "unhealthy"
        assert result["status_code"] == 404


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _EmptySession:
    async def execute(self, *args, **kwargs):
        return _EmptyResult()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestThresholdDefaultsSeededOnEmptyDb:
    @pytest.mark.asyncio
    async def test_库为空时铺内置默认阈值(self, monkeypatch):
        monkeypatch.setattr(threshold_module, "get_db_session", lambda: _EmptySession())
        engine = ThresholdEngine()

        await engine._load_thresholds_from_db_once()

        assert engine._loaded is True
        assert engine._thresholds["thyroid"].confidence_threshold == 0.85
        assert engine._thresholds["liver"].confidence_threshold == 0.88
        assert engine._thresholds["cardiac"].confidence_threshold == 0.92

    @pytest.mark.asyncio
    async def test_库里的行优先于默认值(self, monkeypatch):
        rows = [
            SimpleNamespace(
                organ="thyroid",
                disease_type=None,
                confidence_threshold=0.95,
                description="运维调整",
            )
        ]

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return rows

        class _Session:
            async def execute(self, *args, **kwargs):
                return _Result()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(threshold_module, "get_db_session", lambda: _Session())
        engine = ThresholdEngine()

        await engine._load_thresholds_from_db_once()

        assert engine._thresholds["thyroid"].confidence_threshold == 0.95
        assert engine._thresholds["thyroid"].source == "database"
        # 未被库覆盖的器官仍用设计默认值
        assert engine._thresholds["liver"].confidence_threshold == 0.88
