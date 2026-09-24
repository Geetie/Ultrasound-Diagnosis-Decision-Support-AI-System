"""T-GB-LLM-01 模型热重载编排单元测试。

覆盖：
- 三档碎片化处理（正常 / Warning / 触发）；
- 无活跃会话立即执行；有活跃会话延迟至会话结束；
- 延迟超过上限后强制执行（forced）；
- 单飞：重载在途时不重复触发；
- 适配器异常不抛出、计入失败并写 0 成功率指标；
- 指标写入（成功率 + 耗时）；
- 适配器工厂与现场注入；
- GPU 监控模块在超临界阈值时触发（含显式关闭）。
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from app.inference.hot_reload import (
    HotReloadController,
    LocalFakeReloadAdapter,
    get_reload_adapter,
    reset_hot_reload_controller,
    set_reload_adapter,
)


# ----------------------------------------------------------------------
# 桩件
# ----------------------------------------------------------------------


class _CountingSleep:
    """记录调用次数的休眠替身（不真的等待）。"""

    def __init__(self, release_after: Optional[int] = None) -> None:
        self.calls = 0
        self._release_after = release_after

    async def __call__(self, seconds: float) -> None:
        self.calls += 1


class _SessionProvider:
    """可编排活跃会话数的 provider。"""

    def __init__(self, sequence: List[int]) -> None:
        self._sequence = list(sequence)
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        if len(self._sequence) > 1:
            return self._sequence.pop(0)
        return self._sequence[0]


class _FakeCollector:
    def __init__(self) -> None:
        self.success_rates: List[float] = []
        self.durations: List[float] = []

    def set_model_hot_reload_success_rate(self, rate: float) -> None:
        self.success_rates.append(rate)

    def record_model_hot_reload_duration(self, seconds: float) -> None:
        self.durations.append(seconds)


class _FailingAdapter:
    async def reload(self, *, reason: str) -> bool:
        raise RuntimeError("gpu oom")

    async def health(self) -> bool:
        return True


def _controller(
    *,
    adapter: Any = None,
    sessions: Any = 0,
    collector: Optional[_FakeCollector] = None,
    max_defer: float = 600.0,
    sleep: Any = None,
) -> HotReloadController:
    provider = sessions if callable(sessions) else (lambda: sessions)
    return HotReloadController(
        adapter=adapter if adapter is not None else LocalFakeReloadAdapter(),
        active_sessions_provider=provider,
        warning_threshold=0.30,
        trigger_threshold=0.40,
        max_defer_seconds=max_defer,
        poll_interval_seconds=0.001,
        metrics_factory=(lambda: collector) if collector else (lambda: _FakeCollector()),
        sleep=sleep,
    )


# ----------------------------------------------------------------------
# 阈值分档
# ----------------------------------------------------------------------


class TestThresholds:
    @pytest.mark.asyncio
    async def test_below_warning_does_nothing(self):
        adapter = LocalFakeReloadAdapter()
        ctrl = _controller(adapter=adapter)
        outcome = await ctrl.on_fragmentation_sample(0.10)
        assert outcome.triggered is False
        assert adapter.calls == 0
        assert ctrl.stats().warnings == 0

    @pytest.mark.asyncio
    async def test_above_warning_below_trigger_only_warns(self):
        adapter = LocalFakeReloadAdapter()
        ctrl = _controller(adapter=adapter)
        outcome = await ctrl.on_fragmentation_sample(0.35)
        assert outcome.triggered is False
        assert adapter.calls == 0
        assert ctrl.stats().warnings == 1

    @pytest.mark.asyncio
    async def test_above_trigger_reloads(self):
        adapter = LocalFakeReloadAdapter()
        ctrl = _controller(adapter=adapter)
        outcome = await ctrl.on_fragmentation_sample(0.45)
        assert outcome.triggered is True
        assert outcome.executed is True
        assert outcome.success is True
        assert adapter.calls == 1
        assert "fragmentation" in adapter.reasons[0]

    def test_trigger_must_exceed_warning(self):
        with pytest.raises(ValueError):
            HotReloadController(
                adapter=LocalFakeReloadAdapter(),
                warning_threshold=0.5,
                trigger_threshold=0.4,
            )


# ----------------------------------------------------------------------
# 活跃会话
# ----------------------------------------------------------------------


class TestActiveSessions:
    @pytest.mark.asyncio
    async def test_no_active_session_executes_immediately(self):
        adapter = LocalFakeReloadAdapter()
        ctrl = _controller(adapter=adapter, sessions=0)
        outcome = await ctrl.on_fragmentation_sample(0.5)
        assert outcome.executed is True
        assert outcome.deferred is False
        assert outcome.forced is False
        assert adapter.calls == 1

    @pytest.mark.asyncio
    async def test_defers_until_sessions_finish(self):
        adapter = LocalFakeReloadAdapter()
        # 探测顺序：2（首次）→ 1 → 0（空闲后执行）
        provider = _SessionProvider([2, 1, 0])
        ctrl = _controller(adapter=adapter, sessions=provider)
        outcome = await ctrl.on_fragmentation_sample(0.5)

        assert outcome.deferred is True
        assert outcome.forced is False
        assert outcome.executed is True
        assert adapter.calls == 1
        assert provider.calls >= 3
        assert ctrl.stats().deferred == 1

    @pytest.mark.asyncio
    async def test_forced_after_max_defer(self):
        adapter = LocalFakeReloadAdapter()
        sleep = _CountingSleep()
        ctrl = _controller(
            adapter=adapter,
            sessions=3,  # 始终有活跃会话
            max_defer=0.0,  # 立即超时 → 强制
            sleep=sleep,
        )
        outcome = await ctrl.on_fragmentation_sample(0.5)

        assert outcome.deferred is True
        assert outcome.forced is True
        assert outcome.executed is True
        assert adapter.calls == 1
        assert ctrl.stats().forced == 1

    @pytest.mark.asyncio
    async def test_provider_failure_treated_as_idle(self):
        adapter = LocalFakeReloadAdapter()

        def _broken() -> int:
            raise RuntimeError("nav manager down")

        ctrl = _controller(adapter=adapter, sessions=_broken)
        outcome = await ctrl.on_fragmentation_sample(0.5)
        assert outcome.executed is True
        assert adapter.calls == 1


# ----------------------------------------------------------------------
# 单飞与失败降级
# ----------------------------------------------------------------------


class TestSingleFlightAndFailures:
    @pytest.mark.asyncio
    async def test_skips_when_reload_in_flight(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class _SlowAdapter:
            def __init__(self) -> None:
                self.calls = 0

            async def reload(self, *, reason: str) -> bool:
                self.calls += 1
                started.set()
                await release.wait()
                return True

            async def health(self) -> bool:
                return True

        adapter = _SlowAdapter()
        ctrl = _controller(adapter=adapter, sessions=0)

        first = asyncio.ensure_future(ctrl.on_fragmentation_sample(0.5))
        await started.wait()
        second = await ctrl.on_fragmentation_sample(0.5)
        assert second.skipped_reason == "reload_in_flight"
        assert adapter.calls == 1

        release.set()
        assert (await first).executed is True

    @pytest.mark.asyncio
    async def test_adapter_exception_is_contained(self):
        collector = _FakeCollector()
        ctrl = _controller(adapter=_FailingAdapter(), sessions=0, collector=collector)
        outcome = await ctrl.on_fragmentation_sample(0.6)

        assert outcome.executed is True
        assert outcome.success is False
        assert ctrl.stats().failed == 1
        assert collector.success_rates == [0.0]

    @pytest.mark.asyncio
    async def test_adapter_returning_false_counts_as_failure(self):
        adapter = LocalFakeReloadAdapter()
        adapter.fail_next = True
        collector = _FakeCollector()
        ctrl = _controller(adapter=adapter, sessions=0, collector=collector)

        outcome = await ctrl.on_fragmentation_sample(0.6)
        assert outcome.success is False
        assert collector.success_rates == [0.0]


# ----------------------------------------------------------------------
# 指标
# ----------------------------------------------------------------------


class TestMetrics:
    @pytest.mark.asyncio
    async def test_success_rate_and_duration_published(self):
        collector = _FakeCollector()
        ctrl = _controller(
            adapter=LocalFakeReloadAdapter(), sessions=0, collector=collector
        )
        await ctrl.on_fragmentation_sample(0.5)

        assert collector.success_rates == [1.0]
        assert len(collector.durations) == 1
        assert collector.durations[0] >= 0.0

    @pytest.mark.asyncio
    async def test_metric_failure_does_not_break_reload(self):
        def _broken_factory():
            raise RuntimeError("metrics registry down")

        ctrl = HotReloadController(
            adapter=LocalFakeReloadAdapter(),
            active_sessions_provider=lambda: 0,
            warning_threshold=0.3,
            trigger_threshold=0.4,
            metrics_factory=_broken_factory,
        )
        outcome = await ctrl.on_fragmentation_sample(0.5)
        assert outcome.executed is True


# ----------------------------------------------------------------------
# 显式请求与统计
# ----------------------------------------------------------------------


class TestExplicitRequestAndStats:
    @pytest.mark.asyncio
    async def test_request_reload_executes(self):
        adapter = LocalFakeReloadAdapter()
        ctrl = _controller(adapter=adapter, sessions=0)
        outcome = await ctrl.request_reload("manual:ops")
        assert outcome.executed is True
        assert adapter.reasons == ["manual:ops"]

    @pytest.mark.asyncio
    async def test_stats_accumulate(self):
        ctrl = _controller(adapter=LocalFakeReloadAdapter(), sessions=0)
        await ctrl.on_fragmentation_sample(0.1)
        await ctrl.on_fragmentation_sample(0.35)
        await ctrl.on_fragmentation_sample(0.5)

        stats = ctrl.stats().as_dict()
        assert stats["samples"] == 3
        assert stats["warnings"] == 1
        assert stats["triggered"] == 1
        assert stats["executed"] == 1
        assert stats["last_rate"] == 0.5


# ----------------------------------------------------------------------
# 适配器工厂
# ----------------------------------------------------------------------


class TestAdapterFactory:
    def teardown_method(self):
        set_reload_adapter(None)

    def test_local_fake_default(self):
        set_reload_adapter(None)
        settings_obj = MagicMock()
        settings_obj.hot_reload_adapter = "local_fake"
        adapter = get_reload_adapter(settings_obj)
        assert isinstance(adapter, LocalFakeReloadAdapter)

    def test_unknown_adapter_raises(self):
        set_reload_adapter(None)
        settings_obj = MagicMock()
        settings_obj.hot_reload_adapter = "external"
        with pytest.raises(ValueError):
            get_reload_adapter(settings_obj)

    def test_injected_adapter_wins(self):
        injected = LocalFakeReloadAdapter()
        set_reload_adapter(injected)
        settings_obj = MagicMock()
        settings_obj.hot_reload_adapter = "external"
        assert get_reload_adapter(settings_obj) is injected


# ----------------------------------------------------------------------
# GPU 监控接线
# ----------------------------------------------------------------------


class TestMonitorWiring:
    @pytest.mark.asyncio
    async def test_monitor_triggers_reload_above_critical(self, monkeypatch):
        from app.inference import gpu_memory_monitor as gmm

        monitor = gmm.GPUMemoryMonitor(gpu_id=0)
        controller = _controller(adapter=LocalFakeReloadAdapter(), sessions=0)
        monkeypatch.setattr(
            monitor, "_resolve_hot_reload_controller", lambda: controller
        )

        monitor._update_fragmentation_duration(0.55)
        tasks = list(monitor._hot_reload_tasks)
        assert tasks, "超临界阈值应调度热重载任务"
        await asyncio.gather(*tasks)
        assert controller.stats().executed == 1

    @pytest.mark.asyncio
    async def test_monitor_does_not_trigger_below_critical(self, monkeypatch):
        from app.inference import gpu_memory_monitor as gmm

        monitor = gmm.GPUMemoryMonitor(gpu_id=0)
        controller = _controller(adapter=LocalFakeReloadAdapter(), sessions=0)
        monkeypatch.setattr(
            monitor, "_resolve_hot_reload_controller", lambda: controller
        )

        monitor._update_fragmentation_duration(0.35)
        await asyncio.sleep(0)
        assert not monitor._hot_reload_tasks
        assert controller.stats().triggered == 0

    @pytest.mark.asyncio
    async def test_monitor_with_explicit_none_controller_disabled(self):
        from app.inference import gpu_memory_monitor as gmm

        monitor = gmm.GPUMemoryMonitor(gpu_id=0, hot_reload_controller=None)
        assert monitor._resolve_hot_reload_controller() is None
        monitor._update_fragmentation_duration(0.9)
        await asyncio.sleep(0)
        assert not monitor._hot_reload_tasks

    @pytest.mark.asyncio
    async def test_monitor_manual_trigger(self, monkeypatch):
        from app.inference import gpu_memory_monitor as gmm

        monitor = gmm.GPUMemoryMonitor(gpu_id=0)
        adapter = LocalFakeReloadAdapter()
        controller = _controller(adapter=adapter, sessions=0)
        monkeypatch.setattr(
            monitor, "_resolve_hot_reload_controller", lambda: controller
        )

        outcome = await monitor.trigger_hot_reload("manual:test")
        assert outcome is not None
        assert outcome.executed is True
        assert adapter.reasons == ["manual:test"]

    def teardown_method(self):
        reset_hot_reload_controller()
