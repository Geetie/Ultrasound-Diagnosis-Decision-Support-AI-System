#!/usr/bin/env bash
# 恢复主服务 18000（带 ~/uai/.env 环境）
set -u
cd ~/uai/src
set -a
# shellcheck disable=SC1091
source ~/uai/.env
set +a
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export PYTHONPATH=~/uai/src
LOG=/tmp/uvicorn18000.log
setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 --log-level warning > "$LOG" 2>&1 < /dev/null &
disown
for i in $(seq 1 40); do
  sleep 3
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/health 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "MAIN_18000_OK after $((i*3))s"
    exit 0
  fi
done
echo "MAIN_18000_FAILED"
tail -15 "$LOG"
exit 1
