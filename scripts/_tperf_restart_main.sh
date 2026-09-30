#!/usr/bin/env bash
set -u
UAI="$HOME/uai"
PID=$(ss -ltnp 2>/dev/null | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "${PID:-}" ]; then
  kill "$PID"; sleep 4; kill -9 "$PID" 2>/dev/null || true
fi
cd "$UAI/src"
set -a
source "$UAI/.env"
set +a
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18000 > "$UAI/logs/api.log" 2>&1 &
for i in $(seq 1 40); do
  if curl -s --max-time 2 http://127.0.0.1:18000/health >/dev/null 2>&1; then
    echo "healthy after ${i}x2s"; exit 0
  fi
  sleep 2
done
echo "NOT HEALTHY"; tail -8 "$UAI/logs/api.log" | tr -d '\000'
