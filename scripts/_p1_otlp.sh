#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
KEYS='pk-lf-uai-prod:sk-lf-uai-prod-9f2b7c41e8a3d65f0b18c7a92e4d3f56'
B64=$(printf '%s' "$KEYS" | base64 -w0)
if ! grep -q 'OTEL_EXPORTER_OTLP_HEADERS' "$UAI/.env"; then
  cat >> "$UAI/.env" <<EOF
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:30100/api/public/otel
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic $B64
EOF
  echo "OTLP env appended"
fi
grep 'OTEL_EXPORTER_OTLP' "$UAI/.env" | sed 's/Basic .*/Basic ***/'
echo "=== 重启主服务（让 OTLP exporter 生效）==="
bash ~/server_restart_all.sh > /dev/null 2>&1
sleep 8
echo "=== 触发一条诊断 ==="
bash ~/server_link_smoke.sh 2>&1 | grep -E 'status=completed' | head -1
echo "=== 等待 trace 上报 ==="
sleep 15
KEYS2="$KEYS"
curl -s --max-time 10 -u "$KEYS2" "http://127.0.0.1:30100/api/public/traces?limit=2" | head -c 400
