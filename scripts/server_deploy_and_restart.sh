#!/usr/bin/env bash
# 部署本次修复并重启 API（只绑 127.0.0.1:18000）
set -u
UAI="$HOME/uai"
SRC="$UAI/src"
LOG="$UAI/logs/api.log"

# 0) 本机没有 VictoriaMetrics（settings 默认指向 compose 服务名 victoriametrics），
#    关掉推送，避免日志被 "Temporary failure in name resolution" 刷满。
if [ -f "$UAI/.env" ] && ! grep -q '^OBS_VM_PUSH_ENABLED=' "$UAI/.env"; then
  echo 'OBS_VM_PUSH_ENABLED=false' >> "$UAI/.env"
  echo "env: appended OBS_VM_PUSH_ENABLED=false"
fi

# 1) 停旧进程（只停自己的 uvicorn）
pkill -f 'uvicorn app.main:app' && echo "stopped old uvicorn" || echo "no old uvicorn"
sleep 2
ss -ltn | grep -a ':18000' || echo "port 18000 freed"

# 2) 启动
cd "$SRC" || exit 1
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a
export CUDA_VISIBLE_DEVICES=0
echo "===== RESTART $(date '+%F %T') =====" >> "$LOG"
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18000 --log-level info >> "$LOG" 2>&1 &
echo "new pid $!"

# 3) 轮询健康（lifespan 含多个 best-effort 初始化，给足时间）
for i in $(seq 1 60); do
  code=$(curl -s -o /tmp/health.json -w '%{http_code}' --max-time 5 \
    http://127.0.0.1:18000/api/v1/health 2>/dev/null || true)
  echo "t=${i} http=${code}"
  if [ -n "$code" ] && [ "$code" != "000" ]; then
    echo "--- body ---"
    head -c 900 /tmp/health.json
    echo
    break
  fi
  sleep 3
done

echo
echo "--- 新启动段日志（去 NUL）---"
tr -d '\000' < "$LOG" | sed -n '/===== RESTART/,$p' | tail -60
echo "=== done ==="
