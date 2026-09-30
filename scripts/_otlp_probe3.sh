#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
cd "$UAI/src"
PYTHONPATH="$UAI/src" "$UAI/venv/bin/python" - <<'EOF'
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry import trace

basic = ""
for line in open("/mnt/ee16/home/student03/uai/.env"):
    if line.startswith("OBS_TRACING_OTLP_HEADERS="):
        basic = line.strip().split("=", 1)[1].split("Basic ", 1)[-1]

provider = TracerProvider()
exporter = OTLPSpanExporter(
    endpoint="http://127.0.0.1:30100/api/public/otel/v1/traces",
    headers={"Authorization": f"Basic {basic}"},
)
provider.add_span_processor(SimpleSpanProcessor(exporter))
tracer = trace.get_tracer("probe-sync")
with tracer.start_as_current_span("langfuse-probe-sync") as span:
    span.set_attribute("probe", "sync")
print("sync export done")
EOF
sleep 2
bash /tmp/_langfuse_check.sh 2>&1 | tail -3
