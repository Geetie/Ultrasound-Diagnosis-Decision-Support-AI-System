"""API接口轨道测试专用配置

提供：
1. 实时进度可视化报告器（APIProgressReporter）
2. API专用fixture（共享TestClient、ConnectionManager、全局状态清理）
3. 测试阶段自动分类
4. TestClient生命周期mock：跳过Redis/SG/Consent等外部服务连接
"""
import logging
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from tests.utils.track_progress_reporter import TrackProgressReporter

logger = logging.getLogger(__name__)

PHASE_ICONS = {
    "endpoint": "\U0001f310",
    "auth": "\U0001f510",
    "consent": "\U0001f4cb",
    "diagnosis": "\U0001f3e5",
    "imaging": "\U0001f4f7",
    "dicom": "\U0001f4e1",
    "websocket": "\U0001f50c",
    "rate_limit": "\u23f1",
    "error": "\u274c",
    "safety": "\U0001f6e1",
    "session": "\U0001f511",
    "repository": "\U0001f4c1",
    "schema": "\U0001f4d1",
    "enum": "\U0001f522",
    "feedback": "\U0001f4ac",
    "fhir": "\U0001f3e5",
    "idempotenc": "\u23f1",
    "default": "\u25cf",
}


class APIProgressReporter(TrackProgressReporter):
    PHASE_ICONS = PHASE_ICONS

    def __init__(self):
        super().__init__(track_name="API")


def pytest_configure(config):
    config.pluginmanager.register(APIProgressReporter(), "api_progress")


def _make_redis_mock():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.setex = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=1)
    redis_mock.exists = AsyncMock(return_value=0)
    redis_mock.expire = AsyncMock(return_value=True)
    redis_mock.incr = AsyncMock(return_value=1)
    redis_mock.pipeline = MagicMock(return_value=AsyncMock())
    return redis_mock


def _patch_lifespan_deps():
    patches = [
        patch("app.config.redis.RedisPool.get_client", new_callable=AsyncMock, return_value=_make_redis_mock()),
        patch("app.config.redis.RedisPool.close", new_callable=AsyncMock),
        patch("app.services.safety_gateway_client.safety_gateway_client.warmup", new_callable=AsyncMock),
        patch("app.services.safety_gateway_client.safety_gateway_client.start_health_probe", new_callable=AsyncMock),
        patch("app.services.safety_gateway_client.safety_gateway_client.close", new_callable=AsyncMock),
        patch("app.api.websocket.nav.manager.start_redis_subscriber", new_callable=AsyncMock),
        patch("app.api.websocket.nav.manager.stop_redis_subscriber", new_callable=AsyncMock),
    ]
    return patches


@pytest.fixture(scope="session")
def test_client():
    from fastapi.testclient import TestClient
    from app.main import app

    logger.info("api_test_client_init_start")
    patches = _patch_lifespan_deps()
    started = []
    for p in patches:
        started.append(p.start())

    try:
        client = TestClient(app)
        logger.info("api_test_client_init_done")
        yield client
    finally:
        for p in patches:
            p.stop()
        logger.info("api_test_client_teardown_done")


@pytest.fixture
def manager():
    from app.api.websocket.nav import ConnectionManager
    return ConnectionManager()


@pytest.fixture(autouse=True)
def reset_rate_limit_buckets():
    try:
        from app.config.rate_limit import _buckets
        _buckets.clear()
    except ImportError:
        pass
    yield
    try:
        from app.config.rate_limit import _buckets
        _buckets.clear()
    except ImportError:
        pass


@pytest.fixture
def mock_db_session():
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import AsyncSession
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def checkpoint(request):
    def _checkpoint(name, result, detail=""):
        status = "\u2705" if result else "\u274c"
        print(f"  {status} CHECKPOINT: {name} | {detail}")
    return _checkpoint
