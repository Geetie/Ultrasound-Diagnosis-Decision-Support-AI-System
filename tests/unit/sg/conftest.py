"""SG安全网关轨道测试专用配置

提供：
1. 实时进度可视化报告器（SGProgressReporter）
2. SG专用fixture（融合层mock、单例重置）
3. 测试阶段自动分类
4. 源码变更检测：重型测试在源码未变时自动跳过
"""
import logging
import os
from unittest.mock import AsyncMock, patch

import pytest

logger = logging.getLogger(__name__)

from tests.utils.track_progress_reporter import SourceChangeDetector, TrackProgressReporter

PHASE_ICONS = {
    "desensitization": "\U0001f6e1",
    "contraindication": "\u2695",
    "threshold": "\U0001f4cf",
    "checker": "\U0001f50d",
    "circuit": "\U0001f50c",
    "health": "\U0001f4cb",
    "consent": "\U0001f512",
    "degraded": "\u26a0",
    "redis": "\U0001f504",
    "singleton": "\U0001f511",
    "default": "\u25cf",
}

_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

SG_SOURCE_FILES = [
    os.path.join("safety_gateway", "checker", "orchestrator.py"),
    os.path.join("safety_gateway", "checker", "desensitization_engine.py"),
]

_source_detector = SourceChangeDetector(_CACHE_DIR, {"sg": SG_SOURCE_FILES})


class SGProgressReporter(TrackProgressReporter):
    PHASE_ICONS = PHASE_ICONS

    def __init__(self):
        super().__init__(track_name="SG")


def pytest_configure(config):
    logger.info("SG conftest loaded")
    config.pluginmanager.register(SGProgressReporter(), "sg_progress")
    config.addinivalue_line(
        "markers", "sg_heavy: SG heavy tests (skipped when source unchanged)"
    )
    config.addinivalue_line(
        "markers", "skip_fusion_mock: Skip the autouse fusion health check mock"
    )


def pytest_collection_modifyitems(session, config, items):
    logger.info("Source change detection: checking %d items", len(items))
    _source_detector.check_and_skip(
        items, config,
        env_var="SKIP_SG_UNCHANGED",
        marker_name="sg_heavy",
        key="sg",
        source_files=SG_SOURCE_FILES,
    )


@pytest.fixture(autouse=True)
def mock_sg_fusion_health_check(request):
    if request.node.get_closest_marker("skip_fusion_mock"):
        logger.info("Skipping fusion health check mock for %s (skip_fusion_mock marker)", request.node.name)
        yield
        return
    logger.info("Applying fusion health check mock for %s", request.node.name)
    with patch("safety_gateway.checker.orchestrator._get_health_checker") as mock_getter:
        mock_hc = AsyncMock()
        mock_hc.get_fusion_availability = AsyncMock(return_value=True)
        mock_hc.record_latency = AsyncMock()
        mock_hc.record_e2e_latency = AsyncMock()
        mock_getter.return_value = mock_hc
        yield


@pytest.fixture(autouse=True)
def reset_sg_singletons():
    yield
    logger.info("Resetting SG singletons after test")
    try:
        from safety_gateway.checker.desensitization_engine import SafetyGatewayDesensitizationEngine
        SafetyGatewayDesensitizationEngine._instance = None
        logger.info("Reset SafetyGatewayDesensitizationEngine._instance to None")
    except (ImportError, AttributeError):
        pass


@pytest.fixture
def checkpoint(request):
    def _checkpoint(name, result, detail=""):
        status = "\u2705" if result else "\u274c"
        print(f"  {status} CHECKPOINT: {name} | {detail}")
    return _checkpoint
