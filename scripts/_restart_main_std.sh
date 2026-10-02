#!/usr/bin/env bash
# 标准主服务重启（SKILL 姿势）：杀旧（lstart 核对）→ source .env → 起
set -u
UAI="$HOME/uai"
PID=$(ss -ltnp 2>/dev/null | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$PID" ] && { kill "$PID"; sleep 4; kill -9 "$PID" 2>/dev/null; }
cd "$UAI/src" || exit 1
set -a
# shellcheck disable=SC1091
source "$UAI/.env"
set +a
PYTHONPATH="$UAI/src" nohup "$UAI/venv/bin/python" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18000 \
  > "$UAI/logs/api.log" 2>&1 &
echo "main-restarting"
