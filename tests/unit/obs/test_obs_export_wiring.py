"""可观测性导出链路的接线守卫。

## 为什么有这条测试

2026-09-28 接线审计发现：`MetricsCollector.start_metrics_server()` 只被测试调用、
`VictoriaMetricsPusher` 在 `app/` 内没有任何实例化点 —— 也就是说**能力存在但没人调用**：
运行期没有 `/metrics` 监听、VM 收不到推送，看板面板再正确也永远没有数据。

5611 个测试全绿也没发现它，因为测试测的是"被调用的函数"，不是"有没有人调用"。
所以这里除了测 `wiring` 模块本身，还**显式断言 `app/main.py` 的 lifespan 真的调用了它**。
"""
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import settings
from app.observability import wiring

MAIN_PY = Path(__file__).resolve().parents[3] / "app" / "main.py"


@pytest.fixture
def app_obj():
    return SimpleNamespace(state=SimpleNamespace())


@pytest.fixture
def patched(monkeypatch):
    """替换收集器与推送器，返回两者的替身。"""
    collector = MagicMock()
    collector.start_metrics_server = MagicMock()

    class _FakePusher:
        instances: ClassVar[list] = []

        def __init__(self, *args, **kwargs):
            self.start = AsyncMock()
            self.stop = AsyncMock()
            _FakePusher.instances.append(self)

    _FakePusher.instances = []
    monkeypatch.setattr(
        "app.observability.metrics_collector.collector.MetricsCollector.get_instance",
        staticmethod(lambda: collector),
    )
    monkeypatch.setattr(
        "app.observability.metrics_collector.pusher.VictoriaMetricsPusher", _FakePusher
    )
    monkeypatch.setattr(settings, "obs_metrics_server_enabled", True, raising=False)
    monkeypatch.setattr(settings, "obs_vm_push_enabled", True, raising=False)
    monkeypatch.setattr(
        settings, "obs_victoriametrics_url", "http://vm:8428", raising=False
    )
    return SimpleNamespace(collector=collector, pusher_cls=_FakePusher)


class TestStartObsExport:
    async def test_starts_metrics_server_and_pusher(self, app_obj, patched):
        await wiring.start_obs_export(app_obj)

        patched.collector.start_metrics_server.assert_called_once_with()
        assert len(patched.pusher_cls.instances) == 1
        patched.pusher_cls.instances[0].start.assert_awaited_once_with()
        assert app_obj.state.metrics_collector is patched.collector
        assert app_obj.state.vm_pusher is patched.pusher_cls.instances[0]

    async def test_skips_metrics_server_when_disabled(self, app_obj, patched, monkeypatch):
        monkeypatch.setattr(settings, "obs_metrics_server_enabled", False, raising=False)

        await wiring.start_obs_export(app_obj)

        patched.collector.start_metrics_server.assert_not_called()
        # 关闭暴露不应连带关闭推送
        assert app_obj.state.vm_pusher is not None

    async def test_skips_pusher_when_disabled(self, app_obj, patched, monkeypatch):
        monkeypatch.setattr(settings, "obs_vm_push_enabled", False, raising=False)

        await wiring.start_obs_export(app_obj)

        assert patched.pusher_cls.instances == []
        assert app_obj.state.vm_pusher is None

    async def test_skips_pusher_when_url_empty(self, app_obj, patched, monkeypatch):
        """URL 为空时不启动推送任务：否则后台会一直对着空地址失败重试。"""
        monkeypatch.setattr(settings, "obs_victoriametrics_url", "", raising=False)

        await wiring.start_obs_export(app_obj)

        assert patched.pusher_cls.instances == []
        assert app_obj.state.vm_pusher is None

    async def test_metrics_server_failure_does_not_block_pusher(
        self, app_obj, patched
    ):
        """端口被占用等失败不得连带阻断推送（逐环节 best-effort）。"""
        patched.collector.start_metrics_server.side_effect = OSError("port in use")

        await wiring.start_obs_export(app_obj)

        assert len(patched.pusher_cls.instances) == 1
        patched.pusher_cls.instances[0].start.assert_awaited_once_with()

    async def test_pusher_start_failure_is_contained(self, app_obj, patched, monkeypatch):
        """推送器起不来不得抛出：可观测性不是主服务的可用性前提。"""

        class _BrokenPusher:
            def __init__(self, *args, **kwargs):
                self.start = AsyncMock(side_effect=RuntimeError("no event loop"))
                self.stop = AsyncMock()

        monkeypatch.setattr(
            "app.observability.metrics_collector.pusher.VictoriaMetricsPusher",
            _BrokenPusher,
        )

        await wiring.start_obs_export(app_obj)

        assert app_obj.state.vm_pusher is None


class TestStopObsExport:
    async def test_stops_pusher_and_clears_state(self, app_obj):
        pusher = MagicMock()
        pusher.stop = AsyncMock()
        app_obj.state.vm_pusher = pusher

        await wiring.stop_obs_export(app_obj)

        pusher.stop.assert_awaited_once_with()
        assert app_obj.state.vm_pusher is None

    async def test_stop_without_pusher_is_noop(self, app_obj):
        await wiring.stop_obs_export(app_obj)

        assert app_obj.state.vm_pusher is None

    async def test_stop_failure_is_contained(self, app_obj):
        pusher = MagicMock()
        pusher.stop = AsyncMock(side_effect=RuntimeError("already dead"))
        app_obj.state.vm_pusher = pusher

        await wiring.stop_obs_export(app_obj)

        assert app_obj.state.vm_pusher is None


class TestLifespanActuallyCallsWiring:
    """本次缺陷的**直接对手**：能力存在≠被调用。

    上面测的是 `wiring` 模块内部正确性；即使模块完美，只要 `main.py` 不调用它，
    运行期依然没有指标。所以这里断言 lifespan 里确实存在调用点。
    """

    @pytest.fixture
    def main_src(self) -> str:
        return MAIN_PY.read_text(encoding="utf-8")

    def test_start_is_called_in_lifespan(self, main_src):
        assert "from app.observability.wiring import start_obs_export" in main_src
        assert "await start_obs_export(app)" in main_src

    def test_stop_is_called_in_lifespan(self, main_src):
        assert "from app.observability.wiring import stop_obs_export" in main_src
        assert "await stop_obs_export(app)" in main_src
