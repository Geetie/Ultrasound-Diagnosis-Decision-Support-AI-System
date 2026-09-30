"""OpenTelemetry分布式追踪模块 — 诊断链路追踪与FastAPI自动埋点。

实现DiagnosisSpan上下文管理、compact模式低采样率（<0.1）、
FULL模式强制高采样率（>=0.5），支持FastAPI请求自动追踪。
"""

__all__ = ["DIAGNOSIS_SPANS", "DiagnosisSpan", "init_tracing", "inject_trace_to_structlog", "instrument_fastapi"]

import contextvars
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
        processor = BatchSpanProcessor(exporter)
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
