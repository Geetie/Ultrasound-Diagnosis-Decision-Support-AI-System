#!/usr/bin/env bash
# 启动独立进程 SafetyGateway（设计内独立服务，端口 9001，只绑回环）
set -u
UAI="$HOME/uai"
cd "$UAI/src" || exit 1

set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a
export CUDA_VISIBLE_DEVICES=0

if pgrep -f 'uvicorn safety_gateway.main:app' >/dev/null 2>&1; then
  echo "sg running -> restart"
  pkill -f 'uvicorn safety_gateway.main:app'
  sleep 2
fi

echo "===== SG RESTART $(date '+%F %T') =====" >> "$UAI/logs/sg.log"
nohup "$UAI/venv/bin/python" -m uvicorn safety_gateway.main:app \
  --host 127.0.0.1 --port 9001 --log-level info >> "$UAI/logs/sg.log" 2>&1 &
echo "sg pid $!"

for i in $(seq 1 30); do
  code=$(curl -s -o /tmp/sg.json -w '%{http_code}' --max-time 5 \
    http://127.0.0.1:9001/health 2>/dev/null || true)
  echo "sg t=${i} http=${code}"
  if [ -n "$code" ] && [ "$code" != "000" ]; then
    head -c 700 /tmp/sg.json
    echo
    break
  fi
  sleep 2
done

echo "--- sg.log tail（本次启动段）---"
tr -d '\000' < "$UAI/logs/sg.log" | sed -n '/===== SG RESTART/,$p' | tail -40
echo "=== sg done ==="
