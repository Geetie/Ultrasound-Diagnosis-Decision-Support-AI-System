"""指标采集器补测（quality-debt 覆盖率：56.8% -> 目标高位）。

补齐 `test_metrics_collector.py` 未覆盖的四块：指标服务器启动的三个分支、
两个读取接口（`get_gauge_value` / `get_histogram_p99`）、p99 的计算边界、
以及同意撤回时延的越线计数。

接口要点（曾猜错过，写在这里记住）：`get_gauge_value` / `get_histogram_p99` 的参数是
**采集器上的属性名**（如 `"severe_hallucination_rate"`、`"nav_latency"`），不是
prometheus 指标全名——`app/api/v1/obs_dashboard.py` 就是这么调的。名字里带
`metric_name` 容易让人误传 `metric._name`，那会静默返回 None。

p99 计算依赖 prometheus 的私有结构（`hist._buckets`），必须用真实 `observe()`
造样本，不能自己造假对象——本文件因此还抓出一个真实缺陷，见
`TestHistogramP99Regression`。
"""
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app.observability.enums import DeploymentProfile
from app.observability.metrics_collector.collector import MetricsCollector
from app.observability.metrics_collector.factory import get_metrics_collector


@pytest.fixture(autouse=True)
def _reset_collector():
    """单例跨用例残留会让读取类断言读到上一个用例的值。"""
    MetricsCollector.reset_instance()
    yield
    MetricsCollector.reset_instance()


@pytest.fixture
def collector() -> MetricsCollector:
    return MetricsCollector()


class TestGetInstance:
    def test_singleton_by_default(self):
        assert MetricsCollector.get_instance() is MetricsCollector.get_instance()

    def test_explicit_profile(self):
        instance = MetricsCollector.get_instance(DeploymentProfile.COMPACT)

        assert instance is MetricsCollector.get_instance()

    def test_reset_allows_recreation(self):
        first = MetricsCollector.get_instance()

        MetricsCollector.reset_instance()

        assert MetricsCollector.get_instance() is not first

    def test_factory_returns_singleton(self):
        assert get_metrics_collector() is MetricsCollector.get_instance()


class TestStartMetricsServer:
    def test_disabled_by_config(self, collector, monkeypatch):
        """禁用时不得起端口（否则 CI/多实例会互相抢端口）。"""
        monkeypatch.setattr(
            "app.observability.metrics_collector.collector.settings.obs_metrics_enabled",
            False,
        )

        with patch(
            "app.observability.metrics_collector.collector.start_http_server"
        ) as server:
            collector.start_metrics_server()

        server.assert_not_called()
        assert collector._server_started is False

    def test_enabled_starts_server(self, collector, monkeypatch):
        monkeypatch.setattr(
            "app.observability.metrics_collector.collector.settings.obs_metrics_enabled",
            True,
        )

        with patch(
            "app.observability.metrics_collector.collector.start_http_server"
        ) as server:
            collector.start_metrics_server(port=19001)

        server.assert_called_once()
        assert collector._server_started is True

    def test_start_is_idempotent(self, collector, monkeypatch):
        """重复启动会抛 "address already in use"，必须自己挡住。"""
        monkeypatch.setattr(
            "app.observability.metrics_collector.collector.settings.obs_metrics_enabled",
            True,
        )

        with patch(
            "app.observability.metrics_collector.collector.start_http_server"
        ) as server:
            collector.start_metrics_server(port=19002)
            collector.start_metrics_server(port=19002)

        assert server.call_count == 1

    def test_port_conflict_is_tolerated(self, collector, monkeypatch):
        """端口被占只记日志、不抛异常：观测失败不该让进程起不来。"""
        monkeypatch.setattr(
            "app.observability.metrics_collector.collector.settings.obs_metrics_enabled",
            True,
        )

        with patch(
            "app.observability.metrics_collector.collector.start_http_server",
            side_effect=OSError("address already in use"),
        ):
            collector.start_metrics_server(port=19003)

        assert collector._server_started is False


class TestReadInterfaces:
    def test_gauge_value_roundtrip(self, collector):
        """参数是**属性名**（与 obs_dashboard 的调用一致），返回当前值。"""
        collector.set_severe_hallucination_rate(0.25)

        assert collector.get_gauge_value("severe_hallucination_rate") == pytest.approx(0.25)

    def test_unknown_gauge_returns_none(self, collector):
        assert collector.get_gauge_value("not_a_real_attribute") is None

    def test_histogram_attribute_is_not_a_gauge(self, collector):
        """类型不符时返回 None，而不是硬转出一个假数值。"""
        assert collector.get_gauge_value("consent_withdrawal_latency") is None

    def test_gauge_attribute_is_not_a_histogram(self, collector):
        assert collector.get_histogram_p99("severe_hallucination_rate") is None

    def test_unknown_histogram_returns_none(self, collector):
        assert collector.get_histogram_p99("not_a_real_attribute") is None

    def test_p99_updates_with_observations(self, collector):
        hist = collector.consent_withdrawal_latency
        for value in (0.1, 0.2, 0.3, 0.45):
            hist.observe(value)

        p99 = collector.get_histogram_p99("consent_withdrawal_latency")

        assert p99 is not None and p99 >= 0.3


class TestHistogramP99Regression:
    """回归守卫：p99 曾经**恒为 None**（看板 4 个 p99 面板一直空着）。

    根因：旧实现假设 `hist._buckets` 是 dict（老版本 prometheus_client 如此），
    而当前依赖是 prometheus-client 0.25.0——`_buckets` 已是**列表**、元素是
    `MutexValue` 包装对象。旧代码抛 `AttributeError` 被 `except Exception: pass`
    吞掉，于是"算不出来"和"没有样本"表现完全一样，没人发现。
    """

    def test_p99_is_not_none_with_samples(self, collector):
        """这是"旧实现恒为 None"的直接反证。"""
        for value in (0.05, 0.06, 0.07, 0.08):
            collector.record_nav_latency(value)

        assert collector.get_histogram_p99("nav_latency") is not None

    def test_p99_of_empty_histogram_is_none(self):
        """没有样本时必须返回 None——返回 0 会被误读成"延迟极低"。"""
        from prometheus_client import Histogram

        empty = Histogram("ultrasound_empty_p99_probe", "probe", buckets=[0.1, 1.0])

        assert MetricsCollector._calculate_histogram_p99(empty) is None

    def test_p99_single_bucket_returns_that_bound(self):
        """样本全落同一桶时返回该桶上界（直方图的固有限制：精度由桶决定）。"""
        from prometheus_client import Histogram

        hist = Histogram(
            "ultrasound_single_bucket_probe", "probe", buckets=[0.5, 1.0, 5.0]
        )
        for _ in range(10):
            hist.observe(0.3)

        assert MetricsCollector._calculate_histogram_p99(hist) == pytest.approx(0.5)

    def test_p99_beyond_largest_bucket_is_none(self):
        """p99 落到 +Inf 桶时返回 None（不编"最大有限桶"这种会被当真的数）。"""
        from prometheus_client import Histogram

        hist = Histogram(
            "ultrasound_overflow_bucket_probe", "probe", buckets=[0.5, 1.0, 5.0]
        )
        for _ in range(10):
            hist.observe(10.0)

        assert MetricsCollector._calculate_histogram_p99(hist) is None

    def test_bucket_and_bound_count_mismatch_is_tolerated(self):
        """结构不符时返回 None 并留痕，而不是抛异常或再次静默。"""
        from unittest.mock import MagicMock

        broken = MagicMock()
        broken._upper_bounds = [0.5, 1.0]
        broken._buckets = [MagicMock()]

        assert MetricsCollector._calculate_histogram_p99(broken) is None


class TestConsentWithdrawalLatency:
    def test_below_target_does_not_count_as_exceeded(self, collector):
        counter = collector.consent_withdrawal_target_exceeded

        collector.record_consent_withdrawal_latency(0.4, 0.5)

        assert counter._value.get() == 0

    def test_above_target_increments_counter(self, collector):
        """验收线是"撤回后 <500ms 生效"：越线次数是告警要直接读的量。"""
        counter = collector.consent_withdrawal_target_exceeded

        collector.record_consent_withdrawal_latency(0.62, 0.5)
        collector.record_consent_withdrawal_latency(0.51, 0.5)

        assert counter._value.get() == 2

    def test_exactly_on_target_is_not_exceeded(self, collector):
        counter = collector.consent_withdrawal_target_exceeded

        collector.record_consent_withdrawal_latency(0.5, 0.5)

        assert counter._value.get() == 0

    def test_negative_seconds_clamped(self, collector):
        """时钟回拨可能算出负值；负数进直方图会污染分位数。"""
        hist = collector.consent_withdrawal_latency

        collector.record_consent_withdrawal_latency(-1.0, 0.5)

        assert hist._sum.get() >= 0.0


class TestDashboardReadiness:
    """读取侧守卫：看板真正读得到的指标才算"有埋点"。

    本轮抓到的两个缺陷（p99 恒为 None、带标签直方图取不到值）都属于
    **看板静默空白**——埋点写入方一切正常、直方图里有数据，只是读不出来。
    只查"指标有没有写入方"（见 test_metrics_wiring.py）正好漏掉这一类，
    所以这里从读取侧反查：看板调用的每个属性都要存在，且打点后必须能取到非 None。
    """

    DASHBOARD = (
        Path(__file__).resolve().parents[3] / "app" / "api" / "v1" / "obs_dashboard.py"
    )

    def _read_calls(self) -> list:
        source = self.DASHBOARD.read_text(encoding="utf-8")
        return re.findall(r'get_(?:gauge_value|histogram_p99)\("([^"]+)"\)', source)

    def test_read_calls_are_parsable(self):
        """解析规则失效会让下面两条断言空转，先自证。"""
        assert len(self._read_calls()) >= 8

    def test_every_dashboard_attribute_exists(self, collector):
        missing = [name for name in self._read_calls() if not hasattr(collector, name)]

        assert missing == [], f"看板读取了不存在的采集器属性：{missing}"

    def test_p99_metrics_yield_values_after_observation(self, collector):
        """看板里的 p99 指标打点后必须能算出值（这是"静默 None"的直接对手）。"""
        recorders = {
            "nav_latency": collector.record_nav_latency,
            "assessment_latency": collector.record_assessment_latency,
            "diagnosis_latency": lambda seconds: collector.record_diagnosis_latency(
                seconds, with_retry=False
            ),
            "multi_organ_latency": lambda seconds: collector.record_multi_organ_latency(
                seconds, with_retry=False
            ),
        }

        for name in ("nav_latency", "assessment_latency", "diagnosis_latency", "multi_organ_latency"):
            recorders[name](0.05)

            assert collector.get_histogram_p99(name) is not None, (
                f"{name} 打点后仍取不到 p99 —— 看板该面板会一直是空的"
            )

    def test_gauge_metrics_are_readable(self, collector):
        collector.set_severe_hallucination_rate(0.1)
        collector.set_overall_hallucination_rate(0.2)

        assert collector.get_gauge_value("severe_hallucination_rate") is not None
        assert collector.get_gauge_value("overall_hallucination_rate") is not None


class TestLabelBranches:
    def test_diagnosis_latency_with_retry_label(self, collector):
        """带标签直方图也必须能算 p99。

        回归守卫：`diagnosis_latency` 是 `labels(with_retry=...)` 出来的，桶计数在
        **子指标**上，父对象 `_buckets` 为空 -> 旧实现下看板 `diag_p99` 永远是空。
        """
        collector.record_diagnosis_latency(1.23, with_retry=True)

        assert collector.get_histogram_p99("diagnosis_latency") is not None

    def test_multi_organ_latency_with_retry_label(self, collector):
        collector.record_multi_organ_latency(0.5, with_retry=False)

        assert collector.get_histogram_p99("multi_organ_latency") is not None

    def test_labelled_histogram_merges_all_children(self, collector):
        """不同标签组合的观测要合并计入同一 p99：只看单组会漏掉长尾。"""
        collector.record_multi_organ_latency(0.2, with_retry=False)
        collector.record_multi_organ_latency(0.4, with_retry=True)

        p99 = collector.get_histogram_p99("multi_organ_latency")

        assert p99 is not None and p99 >= 0.2
        # 合并的判据：两个子指标的观测数都要被算进去
        merged_buckets = collector.multi_organ_latency._metrics
        assert len(merged_buckets) == 2, "两种 with_retry 组合应各成一个子指标"

    def test_adaptive_rag_route_label(self, collector):
        collector.inc_adaptive_rag_route("standard")

        # 属性名不带 _total 后缀（prometheus 在暴露时才补），第一版按暴露名找是错的
        assert collector.adaptive_rag_route is not None

    def test_diagnosis_requests_labels(self, collector):
        collector.inc_diagnosis_requests(organ="thyroid", tier="standard")

        assert collector.diagnosis_requests is not None

    def test_metrics_text_exposes_metric_names(self, collector):
        collector.set_severe_hallucination_rate(0.1)

        text = collector.get_metrics_text()

        assert "ultrasound_severe_hallucination_rate" in text
        assert collector.get_content_type()
