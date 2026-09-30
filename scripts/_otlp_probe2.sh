#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
cd "$UAI/src"
PYTHONPATH="$UAI/src" "$UAI/venv/bin/python" - <<'EOF'
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

basic = ""
for line in open("/mnt/ee16/home/student03/uai/.env"):
    if line.startswith("OBS_TRACING_OTLP_HEADERS="):
        basic = line.strip().split("=", 1)[1].split("Basic ", 1)[-1]

provider = TracerProvider()
exporter = OTLPSpanExporter(
    endpoint="http://127.0.0.1:30100/api/public/otel/v1/traces",
    headers={"Authorization": f"Basic {basic}"},
)
provider.add_span_processor(BatchSpanProcessor(exporter))
tracer = trace.get_tracer("probe")
with tracer.start_as_current_span("langfuse-probe") as span:
    span.set_attribute("probe.source", "manual")
print("probe span exported, waiting for batch flush")
import time
time.sleep(6)
EOF
sleep 2
bash /tmp/_langfuse_check.sh 2>&1 | tail -3
