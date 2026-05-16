import pytest
from app.observability.tracing import (
    DiagnosisSpan,
    init_tracing,
    get_tracer,
    get_current_trace_id,
    get_current_span_id,
    set_trace_context,
    clear_trace_context,
    inject_trace_to_structlog,
    shutdown_tracing,
    SPAN_DIAGNOSIS_REQUEST,
    SPAN_DIAGNOSIS_PARSE,
    SPAN_DIAGNOSIS_DISPATCH,
    SPAN_DIAGNOSIS_SPECIALIST,
    SPAN_DIAGNOSIS_RETRIEVAL,
    SPAN_DIAGNOSIS_FUSION,
    SPAN_DIAGNOSIS_DESENSITIZATION,
    SPAN_DIAGNOSIS_OUTPUT,
    DIAGNOSIS_SPANS,
)


@pytest.fixture(autouse=True)
def reset_tracing():
    shutdown_tracing()
    clear_trace_context()
    yield
    shutdown_tracing()
    clear_trace_context()


@pytest.mark.track_obs
class TestSpanConstants:
    def test_diagnosis_spans_count(self):
        assert len(DIAGNOSIS_SPANS) == 8

    def test_span_names_format(self):
        for span in DIAGNOSIS_SPANS:
            assert span.startswith("diagnosis.")

    def test_all_expected_spans_present(self):
        expected = {
            SPAN_DIAGNOSIS_REQUEST,
            SPAN_DIAGNOSIS_PARSE,
            SPAN_DIAGNOSIS_DISPATCH,
            SPAN_DIAGNOSIS_SPECIALIST,
            SPAN_DIAGNOSIS_RETRIEVAL,
            SPAN_DIAGNOSIS_FUSION,
            SPAN_DIAGNOSIS_DESENSITIZATION,
            SPAN_DIAGNOSIS_OUTPUT,
        }
        assert set(DIAGNOSIS_SPANS) == expected


@pytest.mark.track_obs
class TestTraceContext:
    def test_set_and_get_trace_context(self):
        set_trace_context("trace123", "span456")
        assert get_current_trace_id() == "trace123"
        assert get_current_span_id() == "span456"

    def test_clear_trace_context(self):
        set_trace_context("trace123", "span456")
        clear_trace_context()
        assert get_current_trace_id() is None
        assert get_current_span_id() is None

    def test_default_context_is_none(self):
        clear_trace_context()
        assert get_current_trace_id() is None
        assert get_current_span_id() is None

    def test_overwrite_trace_context(self):
        set_trace_context("trace1", "span1")
        set_trace_context("trace2", "span2")
        assert get_current_trace_id() == "trace2"
        assert get_current_span_id() == "span2"


@pytest.mark.track_obs
class TestInjectTraceToStructlog:
    def test_inject_with_trace(self):
        set_trace_context("trace123", "span456")
        event_dict = {}
        result = inject_trace_to_structlog(None, "info", event_dict)
        assert result["trace_id"] == "trace123"
        assert result["span_id"] == "span456"

    def test_inject_without_trace(self):
        clear_trace_context()
        event_dict = {}
        result = inject_trace_to_structlog(None, "info", event_dict)
        assert "trace_id" not in result
        assert "span_id" not in result

    def test_inject_preserves_existing_fields(self):
        set_trace_context("trace123", "span456")
        event_dict = {"existing_field": "value"}
        result = inject_trace_to_structlog(None, "info", event_dict)
        assert result["existing_field"] == "value"
        assert result["trace_id"] == "trace123"


@pytest.mark.track_obs
class TestInitTracing:
    def test_init_with_tracing_disabled(self):
        from app.config.settings import settings as app_settings
        original = app_settings.obs_tracing_enabled
        app_settings.obs_tracing_enabled = False
        try:
            init_tracing()
            tracer = get_tracer()
            assert tracer is None
        finally:
            app_settings.obs_tracing_enabled = original

    def test_shutdown_tracing(self):
        shutdown_tracing()
        assert get_current_trace_id() is None


@pytest.mark.track_obs
class TestDiagnosisSpan:
    @pytest.mark.asyncio
    async def test_span_without_tracer(self):
        async with DiagnosisSpan("diagnosis.request") as span:
            assert span._span is None

    @pytest.mark.asyncio
    async def test_span_set_attribute_without_tracer(self):
        async with DiagnosisSpan("diagnosis.request") as span:
            span.set_attribute("organ", "thyroid")

    @pytest.mark.asyncio
    async def test_span_add_event_without_tracer(self):
        async with DiagnosisSpan("diagnosis.request") as span:
            span.add_event("cache_hit", {"key": "test"})
