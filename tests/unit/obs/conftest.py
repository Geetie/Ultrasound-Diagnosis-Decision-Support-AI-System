"""OBS可观测性轨道测试专用配置

提供：
1. 实时进度可视化报告器（OBSProgressReporter）
2. OBS专用fixture（单例重置、共享配置）
3. 测试阶段自动分类
4. 源码变更检测：重型测试在源码未变时自动跳过
"""
import os

import pytest

from tests.utils.track_progress_reporter import SourceChangeDetector, TrackProgressReporter

PHASE_ICONS = {
    "metrics": "\U0001f4ca",
    "logging": "\U0001f4dd",
    "alert": "\U0001f6a8",
    "escalat": "\U0001f6a8",
    "cusum": "\U0001f4c8",
    "cache": "\U0001f4be",
    "audit": "\U0001f517",
    "collector": "\U0001f4e1",
    "tracing": "\U0001f50d",
    "integration": "\U0001f517",
    "default": "\u25cf",
}

_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

OBS_SOURCE_FILES = [
    os.path.join("app", "observability", "metrics.py"),
    os.path.join("app", "observability", "logging.py"),
    os.path.join("app", "observability", "alert_escalation.py"),
]

_source_detector = SourceChangeDetector(_CACHE_DIR, {"obs": OBS_SOURCE_FILES})


class OBSProgressReporter(TrackProgressReporter):
    PHASE_ICONS = PHASE_ICONS

    def __init__(self):
        super().__init__(track_name="OBS")


def pytest_configure(config):
    config.pluginmanager.register(OBSProgressReporter(), "obs_progress")
    config.addinivalue_line(
        "markers", "obs_heavy: OBS heavy tests (skipped when source unchanged)"
    )


def pytest_collection_modifyitems(session, config, items):
    _source_detector.check_and_skip(
        items, config,
        env_var="SKIP_OBS_UNCHANGED",
        marker_name="obs_heavy",
        key="obs",
        source_files=OBS_SOURCE_FILES,
    )


@pytest.fixture(autouse=True)
def reset_obs_singletons():
    from app.observability import metrics as metrics_module
    from app.observability.cache_monitor import CacheMonitor
    from app.observability.logging import LogStorage
    from app.observability.metrics_collector import MetricsCollector
    from app.observability.tracing import clear_trace_context, shutdown_tracing

    metrics_module._memory_monitor = None
    LogStorage._instance = None
    CacheMonitor.reset_instance()
    MetricsCollector.reset_instance()
    shutdown_tracing()
    clear_trace_context()
    yield
    metrics_module._memory_monitor = None
    LogStorage._instance = None
    CacheMonitor.reset_instance()
    MetricsCollector.reset_instance()
    shutdown_tracing()
    clear_trace_context()


@pytest.fixture
def default_cusum_config():
    from app.observability.cusum import CUSUMConfig
    return CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)


@pytest.fixture
def checkpoint(request):
    def _checkpoint(name, result, detail=""):
        status = "\u2705" if result else "\u274c"
        print(f"  {status} CHECKPOINT: {name} | {detail}")
    return _checkpoint
