"""VictoriaMetrics 推送器补测（quality-debt 覆盖率：32.2% -> 目标高位）。

关注点：这是个**后台循环 + 退避重试**的组件，最容易被"看起来在跑"骗过去。

- `_push_loop` 的成败分支必须分别验证：只测成功路径的话，推送一直失败时
  `fail_count`/`consecutive_failures` 有没有累加、退避有没有生效，全都没人管；
- `_calculate_delay` 的封顶与"熔断"分支要逐个点到：退避不封顶会变成几分钟一次，
  而"熔断"其实是**最长间隔**（不是停止推送）——名字容易误解，用测试钉住语义；
- `stop()` 里 `wait_for` 超时后的取消补偿分支要覆盖，否则任务泄漏没人发现。

确定性做法：`_shutdown_event` 换成"第一次报未关闭、第二次报已关闭"的替身，
让 `_push_loop` **恰好跑一轮**——比 sleep 计时稳，也不会因为 CI 慢而偶发失败。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.observability.metrics_collector.collector import MetricsCollector
from app.observability.metrics_collector.pusher import VictoriaMetricsPusher


class _FakeResponse:
    def __init__(self, status_code=204):
        self.status_code = status_code


class _FakeClient:
    """httpx.AsyncClient 替身：记录 post 的 URL 与载荷。"""

    def __init__(self, response=None, error=None):
        self._response = response or _FakeResponse()
        self._error = error
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, content=None, **kwargs):
        self.posts.append((url, content))
        if self._error is not None:
            raise self._error
        return self._response


def _event_running_once():
    """第一次 is_set() 返回 False（进循环体一次），第二次返回 True（退出）。

    `wait()` 立刻返回，因此不需要真实等待退避时间。
    """
    event = MagicMock()
    event.is_set.side_effect = [False, True]
    event.wait = AsyncMock()
    event.set = MagicMock()
    event.clear = MagicMock()
    return event


@pytest.fixture(autouse=True)
def _reset_collector():
    MetricsCollector.reset_instance()
    yield
    MetricsCollector.reset_instance()


@pytest.fixture
def pusher() -> VictoriaMetricsPusher:
    return VictoriaMetricsPusher(vm_url="http://vm.test:8428", interval=0.01)


class TestInit:
    def test_url_defaults_to_settings(self, monkeypatch):
        monkeypatch.setattr(
            "app.observability.metrics_collector.pusher.settings.obs_victoriametrics_url",
            "http://from-settings:8428",
        )

        assert VictoriaMetricsPusher()._vm_url == "http://from-settings:8428"

    def test_uses_provided_collector(self):
        collector = MetricsCollector()

        assert VictoriaMetricsPusher(collector=collector)._collector is collector

    def test_defaults_to_singleton_collector(self):
        assert VictoriaMetricsPusher()._collector is MetricsCollector.get_instance()


class TestLifecycle:
    async def test_start_creates_task(self, pusher):
        await pusher.start()
        try:
            assert pusher._task is not None and not pusher._task.done()
        finally:
            await pusher.stop()

    async def test_start_is_idempotent(self, pusher):
        """重复 start 会泄漏推送任务，必须自己挡住。"""
        await pusher.start()
        first = pusher._task
        try:
            await pusher.start()
            assert pusher._task is first
        finally:
            await pusher.stop()

    async def test_start_after_stop_creates_new_task(self, pusher):
        await pusher.start()
        first = pusher._task
        await pusher.stop()

        await pusher.start()
        try:
            assert pusher._task is not first
        finally:
            await pusher.stop()

    async def test_stop_without_start_is_safe(self, pusher):
        await pusher.stop()

    async def test_stop_cancels_unresponsive_task(self, pusher):
        """wait_for 超时后必须取消补偿，否则任务一直挂着。"""
        never_ends = asyncio.Event()

        async def _stuck():
            await never_ends.wait()

        pusher._task = asyncio.create_task(_stuck())
        await asyncio.sleep(0)

        with patch(
            "app.observability.metrics_collector.pusher.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            await pusher.stop()

        assert pusher._task.cancelled() or pusher._task.done()


class TestPushLoop:
    async def test_success_resets_consecutive_failures(self, pusher):
        pusher._consecutive_failures = 3
        pusher._shutdown_event = _event_running_once()

        with patch.object(pusher, "_push_metrics", AsyncMock()) as push:
            await pusher._push_loop()

        assert push.await_count == 1
        assert pusher._consecutive_failures == 0

    async def test_failure_accumulates_counters(self, pusher):
        """推送失败必须累加计数与连续失败数（退避与告警都依赖它）。"""
        pusher._shutdown_event = _event_running_once()

        with patch.object(
            pusher, "_push_metrics", AsyncMock(side_effect=RuntimeError("vm down"))
        ):
            await pusher._push_loop()

        assert pusher._fail_count == 1
        assert pusher._consecutive_failures == 1

    async def test_loop_exits_when_shutdown_set(self, pusher):
        pusher._shutdown_event.set()

        with patch.object(pusher, "_push_metrics", AsyncMock()) as push:
            await pusher._push_loop()

        assert push.await_count == 0, "已关闭时不应再推送"


class TestCalculateDelay:
    def test_no_failure_uses_interval(self, pusher):
        assert pusher._calculate_delay() == pytest.approx(pusher._interval)

    def test_first_failure_uses_base_delay(self, pusher):
        pusher._consecutive_failures = 1

        assert pusher._calculate_delay() == pytest.approx(pusher.BASE_RETRY_DELAY)

    def test_backoff_doubles(self, pusher):
        pusher._consecutive_failures = 1
        first = pusher._calculate_delay()
        pusher._consecutive_failures = 2
        second = pusher._calculate_delay()

        assert second == pytest.approx(first * 2)

    def test_backoff_is_capped(self, pusher):
        pusher._consecutive_failures = pusher.MAX_CONSECUTIVE_FAILURES - 1

        assert pusher._calculate_delay() <= pusher.MAX_RETRY_DELAY

    def test_circuit_open_uses_max_delay(self, pusher):
        """达到连续失败阈值后取**最长**间隔（仍在重试，不是停止推送）。

        "circuit_open" 这个名字容易被读成"熔断=不再推送"，实际语义是退避到最大间隔；
        用测试钉住，避免以后有人按字面改成停推。
        """
        pusher._consecutive_failures = pusher.MAX_CONSECUTIVE_FAILURES

        assert pusher._calculate_delay() == pytest.approx(pusher.MAX_RETRY_DELAY)


class TestPushMetrics:
    async def test_204_increments_push_count(self, pusher):
        client = _FakeClient(_FakeResponse(204))

        with patch(
            # httpx 是在 _push_metrics 内部延迟导入的，pusher 模块命名空间里没有它，
            # 只能打真实模块；打 pusher.httpx 会报 AttributeError（我第一版就这么错）。
            "httpx.AsyncClient",
            return_value=client,
        ):
            await pusher._push_metrics()

        assert pusher._push_count == 1
        assert client.posts and client.posts[0][0].endswith("/api/v1/import/prometheus")

    async def test_non_204_counts_as_failure(self, pusher):
        """非 204 必须算失败：VictoriaMetrics 拒绝写入时响应码不是 204。"""
        client = _FakeClient(_FakeResponse(500))

        with patch(
            # httpx 是在 _push_metrics 内部延迟导入的，pusher 模块命名空间里没有它，
            # 只能打真实模块；打 pusher.httpx 会报 AttributeError（我第一版就这么错）。
            "httpx.AsyncClient",
            return_value=client,
        ):
            await pusher._push_metrics()

        assert pusher._push_count == 0
        assert pusher._fail_count == 1

    async def test_exception_propagates_to_loop(self, pusher):
        """网络异常向上抛，由循环统一计数与退避（不在这里吞掉）。"""
        client = _FakeClient(error=RuntimeError("connection refused"))

        with (
            patch(
                # httpx 是在 _push_metrics 内部延迟导入的，pusher 模块命名空间里没有它，
                # 只能打真实模块；打 pusher.httpx 会报 AttributeError（我第一版就这么错）。
                "httpx.AsyncClient",
                return_value=client,
            ),
            pytest.raises(RuntimeError),
        ):
            await pusher._push_metrics()

    async def test_payload_is_metrics_text(self, pusher):
        collector = MetricsCollector()
        collector.set_severe_hallucination_rate(0.42)
        pusher._collector = collector
        client = _FakeClient(_FakeResponse(204))

        with patch(
            # httpx 是在 _push_metrics 内部延迟导入的，pusher 模块命名空间里没有它，
            # 只能打真实模块；打 pusher.httpx 会报 AttributeError（我第一版就这么错）。
            "httpx.AsyncClient",
            return_value=client,
        ):
            await pusher._push_metrics()

        _, content = client.posts[0]
        assert b"ultrasound_severe_hallucination_rate" in content


class TestAccessors:
    def test_get_stats_shape(self, pusher):
        stats = pusher.get_stats()

        assert stats["push_count"] == 0
        assert stats["fail_count"] == 0
        assert stats["vm_url"] == "http://vm.test:8428"
        assert stats["interval"] == pytest.approx(0.01)

    def test_set_interval_affects_delay(self, pusher):
        pusher.set_interval(2.5)

        assert pusher._calculate_delay() == pytest.approx(2.5)
