"""OpenTelemetry分布式追踪模块 — 诊断链路追踪与FastAPI自动埋点。

实现DiagnosisSpan上下文管理、compact模式低采样率（<0.1）、
FULL模式强制高采样率（>=0.5），支持FastAPI请求自动追踪。
"""

__all__ = ["DIAGNOSIS_SPANS", "DiagnosisSpan", "init_tracing", "inject_trace_to_structlog", "instrument_fastapi"]

import contextvars
import re
from typing import Any

import structlog

from app.config.settings import settings
from app.observability.enums import DeploymentProfile

_logger = structlog.get_logger("app.observability.tracing")

_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_trace_id", default=None
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_span_id", default=None
)

_tracer_provider = None
_tracer = None
_initialized = False


SPAN_DIAGNOSIS_REQUEST = "diagnosis.request"
SPAN_DIAGNOSIS_PARSE = "diagnosis.parse"
SPAN_DIAGNOSIS_DISPATCH = "diagnosis.dispatch"
SPAN_DIAGNOSIS_SPECIALIST = "diagnosis.specialist"
SPAN_DIAGNOSIS_RETRIEVAL = "diagnosis.retrieval"
SPAN_DIAGNOSIS_FUSION = "diagnosis.fusion"
SPAN_DIAGNOSIS_DESENSITIZATION = "diagnosis.desensitization"
SPAN_DIAGNOSIS_OUTPUT = "diagnosis.output"

DIAGNOSIS_SPANS = [
    SPAN_DIAGNOSIS_REQUEST,
    SPAN_DIAGNOSIS_PARSE,
    SPAN_DIAGNOSIS_DISPATCH,
    SPAN_DIAGNOSIS_SPECIALIST,
    SPAN_DIAGNOSIS_RETRIEVAL,
    SPAN_DIAGNOSIS_FUSION,
    SPAN_DIAGNOSIS_DESENSITIZATION,
    SPAN_DIAGNOSIS_OUTPUT,
]


def init_tracing() -> None:
    global _tracer_provider, _tracer, _initialized

    if _initialized:
        return

    if not settings.obs_tracing_enabled:
        _logger.info("Tracing disabled by config")
        _initialized = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        profile = DeploymentProfile(settings.deployment_profile)
        sample_rate = settings.obs_tracing_sample_rate
        if profile == DeploymentProfile.FULL:
            sample_rate = max(sample_rate, 0.5)

        sampler = TraceIdRatioBased(rate=sample_rate)

        resource = Resource.create({
            "service.name": "ultrasound-diagnosis-system",
            "service.version": "1.0.0",
            "deployment.profile": settings.deployment_profile,
        })

        provider = SDKTracerProvider(
            resource=resource,
            sampler=sampler,
        )

        _setup_exporter(provider)

        trace.set_tracer_provider(provider)
        _tracer_provider = provider
        _tracer = trace.get_tracer("app.observability")

        _logger.info(
            "Tracing initialized",
            sample_rate=sample_rate,
            profile=profile.value,
        )

    except ImportError:
        _logger.warning("OpenTelemetry packages not installed, tracing disabled")
    except Exception as e:
        _logger.error("Failed to initialize tracing", error=str(e))

    _initialized = True


# ── A.47 PHI 脱敏（官方 mask_otel_spans 实践的自托管映射）──────────────────
# Langfuse 文档：mask 钩子纪律 = 确定性、快速、**抛异常会丢整批导出**——
# 所以这里逐属性 try/except，单个属性失败就替换为占位符（宁缺勿泄）；
# 整体失败由 _MaskedSpan 捕获后返回全占位 attributes。
# 纯 OTel 链路（非 Langfuse SDK）的脱敏位置 = 导出层包装（文档 §「OTel 数据
# 脱敏」：应用内导出前转换，敏感数据不离开进程）。

_PHI_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
    # 长模式在前：18 位证件若排在手机号之后，会被手机号正则吃掉前 11 位
    (re.compile(r"\d{17}[\dXx]"), "[证件号]"),
    (re.compile(r"1[3-9]\d{9}"), "[手机号]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[邮箱]"),
]
try:
    # 医学人名模式（SG 同源：1~4 汉字 + 医学称谓后缀）——SG 包不依赖 app，无循环
    from safety_gateway.desensitization_rules import init_person_name_pattern

    _PHI_PATTERNS.append((init_person_name_pattern(), "[人名]"))
except Exception:  # pragma: no cover —— SG 不可用时其余正则仍生效
    _logger.warning("phi_person_name_pattern_unavailable")


def _mask_phi_text(value: str) -> str:
    """单字符串 PHI 脱敏。确定性 + 快速 + 不抛（官方纪律）。"""
    try:
        for pattern, placeholder in _PHI_PATTERNS:
            value = pattern.sub(placeholder, value)
        return value
    except Exception:  # pragma: no cover —— 脱敏失败宁占位不泄
        return "[MASK_FAILED]"


def _mask_phi_attributes(attributes: Any) -> dict[str, Any] | None:
    """遍历 span attributes，字符串值（含标量序列）做 PHI 脱敏。"""
    if not attributes:
        return None
    masked: dict[str, Any] = {}
    changed = False
    try:
        for key, value in dict(attributes).items():
            if isinstance(value, str):
                new_value = _mask_phi_text(value)
                if new_value != value:
                    changed = True
                masked[key] = new_value
            elif isinstance(value, (list, tuple)) and value and all(
                isinstance(v, str) for v in value
            ):
                new_seq = [_mask_phi_text(v) for v in value]
                if new_seq != list(value):
                    changed = True
                masked[key] = new_seq
            else:
                masked[key] = value
    except Exception:  # pragma: no cover —— 整体失败宁全占位不泄
        return {k: "[MASK_FAILED]" for k in masked} if masked else None
    return masked if changed else None


class _MaskedSpan:
    """ReadableSpan 只读代理：attributes/name 经 PHI 脱敏，其余字段委托原对象。

    OTLPSpanExporter 序列化时读 .name/.attributes/.context 等——代理属性
    重写即可让导出的 protobuf 只含脱敏值（原 span 对象不被修改）。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._masked_attributes = _mask_phi_attributes(getattr(inner, "attributes", None))
        self._masked_name = (
            _mask_phi_text(inner.name) if isinstance(getattr(inner, "name", None), str) else None
        )

    @property
    def name(self) -> str:
        return self._masked_name or self._inner.name

    @property
    def attributes(self) -> Any:
        if self._masked_attributes is not None:
            return self._masked_attributes
        return self._inner.attributes

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class PhiMaskingExporter:
    """OTLP exporter 的 PHI 脱敏包装（export 前转换，敏感数据不离开进程）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def export(self, spans: Any) -> Any:
        try:
            return self._inner.export([_MaskedSpan(s) for s in spans])
        except Exception:  # pragma: no cover —— 脱敏链路失败不影响导出主链
            raise

    def shutdown(self) -> Any:
        return self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> Any:
        return self._inner.force_flush(timeout_millis)


def _setup_exporter(provider) -> None:
    """优先 HTTP OTLP（Langfuse v3 只暴露 HTTP：/api/public/otel/v1/traces），
    HTTP exporter 不可用时回退 gRPC（自建 collector 场景）。
    """
    try:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Langfuse 等接收端要求 Basic auth（pk:sk）时经 headers 传入
        # （显式构造的 exporter 不会自动读 OTEL_EXPORTER_OTLP_HEADERS env）
        headers: dict[str, str] | None = None
        raw_headers = settings.obs_tracing_otlp_headers
        if raw_headers:
            headers = {}
            for part in raw_headers.split(","):
                if "=" in part:
                    k, _, v = part.partition("=")
                    headers[k.strip()] = v.strip()
            # A.60（零静默）：**必须校验 auth 头本身是否成形**。真机实测（2026-10-13）：
            # `~/uai/.env` 里 `OBS_TRACING_OTLP_HEADERS=Authorization=Basic cGst...`
            # 没加引号，启动脚本 `set -a; source .env` 按空格断词 → 进程环境里只剩
            # `Authorization=Basic`（凭据被当成命令丢掉）→ 每次导出都被 Langfuse 401 拒，
            # **而应用侧只有一行 "auth=True" 的 info**（它只判"有没有 headers"，不判内容）——
            # 结果是"tracing 开着、界面上一条 trace 都没有"，排查全靠猜。
            malformed = [
                k for k, v in headers.items()
                if k.lower() == "authorization" and len(v.split(None, 1)[-1]) < 16
            ]
            if malformed:
                _logger.warning(
                    "otlp_auth_header_looks_malformed",
                    header=",".join(malformed),
                    hint=(
                        "Authorization 头疑似被截断（值里含空格时 .env 必须加引号，"
                        "否则 `source .env` 会按空格断词）—— 导出会被接收端 401 拒绝，"
                        "而界面上表现为「一条 trace 都没有」"
                    ),
                )
        endpoint = settings.obs_tracing_otlp_endpoint
        exporter = None
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as _HttpExporter,
            )

            # HTTP exporter 的 endpoint 需为完整 /v1/traces URL
            http_endpoint = endpoint.rstrip("/")
            if not http_endpoint.endswith("/v1/traces"):
                http_endpoint = f"{http_endpoint}/v1/traces"
            exporter = _HttpExporter(endpoint=http_endpoint, headers=headers)
            endpoint = http_endpoint
        except ImportError:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as _GrpcExporter,
            )

            exporter = _GrpcExporter(
                endpoint=endpoint, insecure=True, headers=headers
            )
        processor = BatchSpanProcessor(PhiMaskingExporter(exporter))
        provider.add_span_processor(processor)
        _logger.info(
            "OTLP exporter configured",
            endpoint=endpoint,
            auth=bool(headers),
        )
    except ImportError:
        _logger.warning("OTLP exporter not available, using default")
    except Exception as e:
        _logger.warning("Failed to setup OTLP exporter", error=str(e))


def get_tracer():
    global _tracer
    if _tracer is None:
        init_tracing()
    return _tracer


def get_current_trace_id() -> str | None:
    return _current_trace_id.get()


def get_current_span_id() -> str | None:
    return _current_span_id.get()


def set_trace_context(trace_id: str, span_id: str) -> None:
    _current_trace_id.set(trace_id)
    _current_span_id.set(span_id)


def clear_trace_context() -> None:
    _current_trace_id.set(None)
    _current_span_id.set(None)


class DiagnosisSpan:
    def __init__(
        self,
        span_name: str,
        attributes: dict[str, Any] | None = None,
    ):
        self._span_name = span_name
        self._attributes = attributes or {}
        self._span = None
        self._token_trace = None
        self._token_span = None

    async def __aenter__(self):
        tracer = get_tracer()
        if tracer is None:
            return self

        self._span = tracer.start_span(
            self._span_name,
            attributes=self._attributes,
        )

        from opentelemetry import trace

        ctx = trace.set_span_in_context(self._span)
        span_context = self._span.get_span_context()

        self._token_trace = _current_trace_id.set(
            format(span_context.trace_id, "032x")
        )
        self._token_span = _current_span_id.set(
            format(span_context.span_id, "016x")
        )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._span is not None:
            if exc_type is not None:
                self._span.set_status(
                    Status(StatusCode.ERROR, str(exc_val))
                )
                self._span.record_exception(exc_val)
            else:
                self._span.set_status(Status(StatusCode.OK))

            self._span.end()

        if self._token_trace is not None:
            _current_trace_id.reset(self._token_trace)
        if self._token_span is not None:
            _current_span_id.reset(self._token_span)

        return False

    def set_attribute(self, key: str, value: Any) -> None:
        if self._span is not None:
            self._span.set_attribute(key, value)

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        if self._span is not None:
            self._span.add_event(name, attributes or {})


try:
    from opentelemetry.trace import Status, StatusCode
except ImportError:
    class StatusCode:
        OK = "OK"
        ERROR = "ERROR"

    class Status:
        def __init__(self, code, description=None):
            self.code = code
            self.description = description


def inject_trace_to_structlog(logger, method_name, event_dict):
    trace_id = get_current_trace_id()
    span_id = get_current_span_id()
    if trace_id:
        event_dict["trace_id"] = trace_id
    if span_id:
        event_dict["span_id"] = span_id
    return event_dict


def instrument_fastapi(app) -> None:
    if not settings.obs_tracing_enabled:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        _logger.info("FastAPI instrumented for tracing")
    except ImportError:
        _logger.warning("FastAPI instrumentation not available")
    except Exception as e:
        _logger.error("Failed to instrument FastAPI", error=str(e))


def shutdown_tracing() -> None:
    global _tracer_provider, _tracer, _initialized

    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
        except Exception as e:
            _logger.error("Failed to shutdown tracer provider", error=str(e))

    _tracer_provider = None
    _tracer = None
    _initialized = False
    _logger.info("Tracing shutdown complete")
