"""T-G1-INT-03：性能基准套件的并发维度与显存维度。

不变量：
1. 并发维度的结论必须来自「并发 P99 / 串行 P99」，且与门槛比较结果一致；
2. 显存维度在本机无 GPU 时**必须是 SKIP**——静默 PASS 会让人误以为显存已验证；
3. 两个维度都要能被 --suite 选中。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bench_suite.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bench_suite_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_suite_under_test"] = module
    spec.loader.exec_module(module)
    return module


bench = _load_module()


class TestSuiteRegistry:
    def test_new_suites_are_selectable(self):
        for suite in ("concurrency", "vram"):
            args = bench.parse_args(["--suite", suite])
            assert args.suite == suite

    def test_unknown_suite_rejected(self):
        with pytest.raises(SystemExit):
            bench.parse_args(["--suite", "nope"])

    def test_list_mentions_new_dimensions(self, capsys):
        bench.main(["--list"])
        out = capsys.readouterr().out

        assert "concurrency" in out
        assert "vram" in out


class TestSuiteConcurrency:
    def test_emits_amplification_metric(self):
        report = bench.suite_concurrency(iterations=8, concurrency=2)

        metrics = [item for item in report.metrics if "放大倍数" in item.name]
        assert len(metrics) == 1
        assert metrics[0].unit == "x"
        assert metrics[0].threshold == bench.CONCURRENCY_AMPLIFICATION_LIMIT
        assert f"C={2}" in metrics[0].name

    def test_metric_pass_flag_matches_threshold(self):
        report = bench.suite_concurrency(iterations=8, concurrency=2)
        metric = report.metrics[0]

        assert metric.passed == (metric.value <= metric.threshold)

    def test_serial_baseline_included_as_denominator(self):
        report = bench.suite_concurrency(iterations=8, concurrency=2)

        names = [item.name for item in report.results]
        assert any("串行基线" in name for name in names)
        assert any("并发 P99" in name for name in names)

    def test_amplification_fails_on_strict_threshold(self, monkeypatch):
        """门槛收紧到 0 时必须判失败——证明结论真的来自比值比较。"""
        monkeypatch.setattr(bench, "CONCURRENCY_AMPLIFICATION_LIMIT", 0.0)

        report = bench.suite_concurrency(iterations=8, concurrency=2)

        assert report.metrics[0].passed is False
        assert report.passed is False

    def test_concurrency_level_used_in_naming(self):
        report = bench.suite_concurrency(iterations=6, concurrency=3)

        assert "C=3" in report.results[-1].name
        assert "C=3" in report.metrics[0].name


class _FakeSnapshot:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class _FakeMonitorAvailable:
    def __init__(self, *args, **kwargs):
        pass

    def is_gpu_available(self):
        return True

    def take_snapshot(self):
        return _FakeSnapshot(
            {
                "total_memory_mb": 24000.0,
                "used_memory_mb": 12000.0,
                "fragmentation_rate": 0.12,
            }
        )


class _FakeMonitorNoSnapshot:
    def __init__(self, *args, **kwargs):
        pass

    def is_gpu_available(self):
        return True

    def take_snapshot(self):
        return None


class _FakeMonitorOverloaded:
    def __init__(self, *args, **kwargs):
        pass

    def is_gpu_available(self):
        return True

    def take_snapshot(self):
        return _FakeSnapshot(
            {
                "total_memory_mb": 24000.0,
                "used_memory_mb": 23000.0,
                "fragmentation_rate": 45.0,
            }
        )


@pytest.fixture
def patch_monitor(monkeypatch):
    def _apply(cls):
        import app.inference.gpu_memory_monitor as monitor_module

        monkeypatch.setattr(monitor_module, "GPUMemoryMonitor", cls)

    return _apply


class TestSuiteVram:
    def test_no_gpu_is_skipped_not_passed_silently(self, patch_monitor):
        class _NoGpu:
            def __init__(self, *args, **kwargs):
                pass

            def is_gpu_available(self):
                return False

        patch_monitor(_NoGpu)

        report = bench.suite_vram()

        assert report.metrics == []
        assert report.skipped
        assert "无可用 GPU" in report.skipped[0]

    def test_utilization_metric_computed(self, patch_monitor):
        patch_monitor(_FakeMonitorAvailable)

        report = bench.suite_vram()

        utilization = next(item for item in report.metrics if item.name == "显存占用率")
        assert utilization.value == 50.0
        assert utilization.unit == "%"
        assert utilization.passed is True

    def test_fragmentation_ratio_converted_to_percent(self, patch_monitor):
        patch_monitor(_FakeMonitorAvailable)

        report = bench.suite_vram()

        fragmentation = next(item for item in report.metrics if item.name == "显存碎片率")
        assert fragmentation.value == 12.0  # 0.12 -> 12%

    def test_overloaded_gpu_fails(self, patch_monitor):
        patch_monitor(_FakeMonitorOverloaded)

        report = bench.suite_vram()

        assert report.passed is False
        assert all(item.passed is False for item in report.metrics)

    def test_snapshot_none_is_skipped(self, patch_monitor):
        patch_monitor(_FakeMonitorNoSnapshot)

        report = bench.suite_vram()

        assert report.skipped
        assert report.metrics == []


class TestFragmentationExtraction:
    def test_accepts_ratio_and_percent_forms(self):
        assert bench._vram_fragmentation_rate({"fragmentation_rate": 0.35}) == 35.0
        assert bench._vram_fragmentation_rate({"fragmentation_rate": 45.0}) == 45.0
        assert bench._vram_fragmentation_rate({"fragmentation_percent": 7.5}) == 7.5

    def test_missing_returns_none(self):
        assert bench._vram_fragmentation_rate({}) is None
        assert bench._vram_fragmentation_rate({"fragmentation_rate": "n/a"}) is None


class TestMainWiring:
    def test_inline_also_runs_concurrency(self, capsys):
        """CI 跑的是 --suite inline，并发维度必须自动包含，否则等于没接。"""
        exit_code = bench.main(["--suite", "inline", "--iterations", "8"])

        out = capsys.readouterr().out
        assert "放大倍数" in out
        assert exit_code in (0, 1)  # 退出码随达标情况而定，但必须跑过

    def test_vram_only_run_exits_zero_on_skip(self, capsys):
        exit_code = bench.main(["--suite", "vram"])

        out = capsys.readouterr().out
        assert "SKIP" in out or "PASS" in out
        assert exit_code == 0

    def test_json_export_includes_metrics(self, tmp_path):
        json_path = tmp_path / "bench.json"

        bench.main(
            ["--suite", "inline", "--iterations", "8", "--json", str(json_path)]
        )

        payload = json_path.read_text(encoding="utf-8")
        assert '"metrics"' in payload
        assert "放大倍数" in payload
