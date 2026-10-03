#!/usr/bin/env bash
# 交付验证：完整链路（旁路开）+ 报告正文 + LLM 失败计数
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
export PYTHONPATH=~/uai/src
# ⚠️ 旁路开关必须在**重启之前** export，否则环境变量不进新进程
export NODULE_CONTOUR_IN_PIPELINE_ENABLED=1
export ATTRIBUTE_HINTS_ENABLED=1

say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 1) 带旁路重启主服务 ==="
pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
sleep 3
export CUDA_VISIBLE_DEVICES=0
setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 \
  --log-level warning > /tmp/uvicorn18000.log 2>&1 < /dev/null &
disown
for i in $(seq 1 40); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$c" = "200" ] && { say "  18000 OK ($((i*3))s)"; break; }
done
grep -c NODULE_CONTOUR ~/uai/src/app/config/settings.py >/dev/null 2>&1 || true
PID=$(pgrep -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" | head -1)
say "  旁路开关在进程环境里: $(tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep -c NODULE_CONTOUR_IN_PIPELINE_ENABLED)"

say "=== 2) 跑演练 ==="
IMG=$(ls ~/uai/data/tn3k_dataset/test-image/* 2>/dev/null | head -1)
IMG_PATH="$IMG" ~/uai/venv/bin/python ~/uai/src/scripts/deploy_drill_e2e.py 2>&1 | tail -12

say "=== 3) LLM 失败计数（0 才算过）==="
for k in generation_failed specialist_llm_failed llm_unavailable; do
  say "  $k = $(grep -c "$k" /tmp/uvicorn18000.log 2>/dev/null || echo 0)"
done
say "=== 4) 成功生成的痕迹 ==="
grep -iE "llm_ok|generation_success|dispatcher_route" /tmp/uvicorn18000.log 2>/dev/null \
  | tail -5 | cut -c1-170
say "DONE"
