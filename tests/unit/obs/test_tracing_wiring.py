"""接线守卫：tracing 与 request_id 必须真的挂在生产入口上（15-技术升级规划 P0-1）。

## 为什么用字符串断言

2026-09-28 真机部署多次出现"能力已实现但未接线"（指标导出、告警回调、双通道 WS）。
行为测试 mock 了被调方就永远绿，拦不住"main.py 忘了调用"这类断链 —— 唯一根治手段
是对 main.py / storage.py 源码做静态断言（与 test_obs_export_wiring.py 同一范式）。
"""
from pathlib import Path

import pytest

MAIN_PY = Path(__file__).resolve().parents[3] / "app" / "main.py"
STORAGE_PY = Path(__file__).resolve().parents[3] / "app" / "observability" / "logging" / "storage.py"
ERRORS_PY = Path(__file__).resolve().parents[3] / "app" / "api" / "v1" / "errors.py"


@pytest.fixture(scope="module")
def main_src() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def storage_src() -> str:
    return STORAGE_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def errors_src() -> str:
    return ERRORS_PY.read_text(encoding="utf-8")


class TestTracingActuallyWiredInMain:
    def test_main_imports_request_id_middleware(self, main_src):
        assert "from app.observability.request_id import RequestIdMiddleware" in main_src

    def test_init_tracing_called_in_lifespan(self, main_src):
        assert "from app.observability.tracing import init_tracing" in main_src
        assert "init_tracing()" in main_src

    def test_shutdown_tracing_called_in_lifespan(self, main_src):
        assert "from app.observability.tracing import shutdown_tracing" in main_src
        assert "shutdown_tracing()" in main_src

    def test_instrument_fastapi_called_before_routers(self, main_src):
        assert "from app.observability.tracing import instrument_fastapi" in main_src
        assert "instrument_fastapi(app)" in main_src
        # 顺序约束：instrument 必须在首个 include_router 之前
        idx_instrument = main_src.index("instrument_fastapi(app)")
        idx_router = main_src.index("app.include_router(")
        assert idx_instrument < idx_router

    def test_request_id_middleware_registered(self, main_src):
        assert "app.add_middleware(RequestIdMiddleware)" in main_src


class TestStructlogChainCarriesContext:
    def test_storage_chain_has_merge_contextvars(self, storage_src):
        assert "structlog.contextvars.merge_contextvars" in storage_src

    def test_storage_chain_has_trace_injection(self, storage_src):
        assert "inject_trace_to_structlog" in storage_src


class TestErrorHandlersCarryRequestId:
    def test_handlers_read_contextvar(self, errors_src):
        assert "from app.observability.request_id import get_current_request_id" in errors_src
        assert errors_src.count("get_current_request_id()") >= 3


class TestLangfuseStackConfig:
    """Langfuse 自托管栈的**配置守卫**（A.60）。

    现场证据（2026-10-13 真机）：Langfuse 六个容器 Up，界面正常，但**一条 trace 都没有**。
    根因有二，都属"配置错了但没人看"：
      ① ``scripts/langfuse/docker-compose.yml`` 里 ``REDIS_URL: redis://redis:6379``
         **漏了密码**，而 redis 服务带 ``--requirepass`` → web 容器
         "Failed to instantiate otel ingestion queue" → 每条导出被 500 拒（静默）；
      ② 部署侧 ``.env`` 的 OTLP Basic 头**手抄错一位**（pk 少/差一个字符）→ 401。
    这条守卫钉住 ①（仓库内可防）；②由 tracing.py 的"头看起来被截断"告警提示。
    """

    COMPOSE = (
        Path(__file__).resolve().parents[3] / "scripts" / "langfuse" / "docker-compose.yml"
    )
    TRACING = (
        Path(__file__).resolve().parents[3] / "app" / "observability" / "tracing.py"
    )

    def test_redis_url_carries_password(self):
        src = self.COMPOSE.read_text(encoding="utf-8")
        assert "--requirepass ${REDIS_PASSWORD}" in src, "redis 服务应带密码（否则任何人可连）"
        line = [ln for ln in src.splitlines() if "REDIS_URL:" in ln]
        assert line, "compose 里应有 REDIS_URL"
        assert "@redis:6379" in line[0] and "${REDIS_PASSWORD}@" in line[0], (
            "REDIS_URL 必须带密码（redis://:${REDIS_PASSWORD}@redis:6379）—— "
            "漏密码会让 Langfuse 起不来 OTLP 摄取队列，表现为「tracing 开着但一条 trace 都没有」"
        )

    def test_malformed_auth_header_is_warned_not_silent(self):
        src = self.TRACING.read_text(encoding="utf-8")
        assert "otlp_auth_header_looks_malformed" in src, (
            "OTLP Authorization 头疑似被截断时必须告警（否则表现为「界面里一条 trace 都没有」）"
        )
