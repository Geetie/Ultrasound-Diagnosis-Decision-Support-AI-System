#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
grep -q 'OBS_TRACING_ENABLED' "$UAI/.env" || echo 'OBS_TRACING_ENABLED=true' >> "$UAI/.env"
grep 'OBS_TRACING' "$UAI/.env"
cd "$UAI/src"
git add -A 2>/dev/null
bash ~/server_restart_all.sh > /dev/null 2>&1
sleep 8
echo "=== 冒烟（产生 trace）==="
bash ~/server_link_smoke.sh 2>&1 | grep -E 'status=completed' | head -1
echo "=== 等 BatchSpanProcessor flush ==="
sleep 20
KEYS='pk-lf-uai-prod:sk-lf-uai-prod-9f2b7c41e8a3d65f0b18c7a92e4d3f56'
curl -s --max-time 10 -u "$KEYS" "http://127.0.0.1:30100/api/public/traces?limit=3" | head -c 500
