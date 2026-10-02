#!/bin/bash
# 修复 SG fusion 探针误报：src/.env 缺 FUSION_LAYER_PORT → SG 用默认 8000 → connection_refused
set -u
UAI="$HOME/uai"
SRC="$UAI/src"

echo "=== 1. 确认 src/.env 现状 ==="
grep -c 'FUSION_LAYER_PORT' "$SRC/.env" 2>/dev/null || echo "0 (missing)"

echo "=== 2. 补配置 ==="
if ! grep -q 'FUSION_LAYER_PORT' "$SRC/.env" 2>/dev/null; then
  cp "$SRC/.env" "$SRC/.env.bak_fusion"
  printf '\nFUSION_LAYER_PORT=18000\n' >> "$SRC/.env"
  echo "appended FUSION_LAYER_PORT=18000 to src/.env"
fi
tail -3 "$SRC/.env"

echo "=== 3. 重启 SG（从 src 目录，读 src/.env）==="
for pid in $(pgrep -f 'safety_gateway.main'); do
  kill -9 "$pid" 2>/dev/null && echo "killed $pid"
done
sleep 2
cd "$SRC" && export PYTHONPATH="$SRC"
nohup "$UAI/venv/bin/python" -m uvicorn safety_gateway.main:app \
  --host 127.0.0.1 --port 9001 --log-level info \
  > "$UAI/logs/safety_gateway.log" 2>&1 &
for i in $(seq 1 20); do
  curl -s --max-time 3 http://127.0.0.1:9001/health | grep -q status && break
  sleep 3
done
echo "=== 4. 验证 fusion_layer 组件状态 ==="
curl -s --max-time 3 http://127.0.0.1:9001/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('overall:', d['status']); print('fusion:', d['components']['fusion_layer'])"
curl -s -o /dev/null -w '18000=%{http_code}\n' --max-time 3 http://127.0.0.1:18000/api/v1/health
echo "=== DONE ==="
