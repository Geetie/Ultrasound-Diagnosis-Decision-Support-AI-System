#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
# 1) 取 compose 里的 project keys 计算 Basic auth
PK=$(grep -oP 'LANGFUSE_INIT_PROJECT_PUBLIC_KEY:\s*"\K[^"]+' "$UAI/src/scripts/langfuse/docker-compose.yml" 2>/dev/null || grep -oP 'LANGFUSE_INIT_PROJECT_PUBLIC_KEY:\s*"\K[^"]+' "$UAI/langfuse/docker-compose.yml")
SK=$(grep -oP 'LANGFUSE_INIT_PROJECT_SECRET_KEY:\s*"\K[^"]+' "$UAI/src/scripts/langfuse/docker-compose.yml" 2>/dev/null || grep -oP 'LANGFUSE_INIT_PROJECT_SECRET_KEY:\s*"\K[^"]+' "$UAI/langfuse/docker-compose.yml")
if [ -z "${PK:-}" ] || [ -z "${SK:-}" ]; then
  PK=$(docker exec langfuse-web-1 printenv LANGFUSE_INIT_PROJECT_PUBLIC_KEY 2>/dev/null || echo pk-lf-uai-prod)
  SK=$(docker exec langfuse-web-1 printenv LANGFUSE_INIT_PROJECT_SECRET_KEY 2>/dev/null || echo sk-lf-uai-prod-9f2b7c41e8a3d65f0b18c7a92e4d3f56)
fi
BASIC=$(printf '%s:%s' "$PK" "$SK" | base64 -w0)
echo "pk=$PK basic=${BASIC:0:20}..."

# 2) 装 HTTP exporter（若缺）
"$UAI/venv/bin/pip" install -q 'opentelemetry-exporter-otlp-proto-http==1.24.0' 2>&1 | tail -1 || true

# 3) .env 幂等写入
python3 - "$BASIC" <<'EOF'
import sys
from pathlib import Path
p = Path.home() / "uai/.env"
lines = p.read_text().splitlines()
basic = sys.argv[1]
want = {
    "OBS_TRACING_ENABLED": "true",
    "OBS_TRACING_OTLP_ENDPOINT": "http://127.0.0.1:30100/api/public/otel",
    "OBS_TRACING_OTLP_HEADERS": f"Authorization=Basic {basic}",
    "OBS_TRACING_SAMPLE_RATE": "1.0",
}
seen = set()
out = []
for l in lines:
    k = l.split("=", 1)[0].strip()
    if k in want:
        out.append(f"{k}={want[k]}")
        seen.add(k)
    else:
        out.append(l)
for k, v in want.items():
    if k not in seen:
        out.append(f"{k}={v}")
p.write_text("\n".join(out) + "\n")
print("env ok")
EOF
grep -E 'OBS_TRACING' "$UAI/.env" | sed 's/Basic .*/Basic ***/'
