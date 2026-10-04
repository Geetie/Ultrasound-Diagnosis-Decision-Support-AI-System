#!/usr/bin/env bash
# 重启主服务（带 .env + 旁路开关）并跑交付门禁
set -u
cd ~/uai/src
set -a
source ~/uai/.env
set +a
export PYTHONPATH=~/uai/src
export NODULE_CONTOUR_IN_PIPELINE_ENABLED=1
export ATTRIBUTE_HINTS_ENABLED=1

pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
sleep 4
export CUDA_VISIBLE_DEVICES=0
setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 \
  --log-level warning > /tmp/uvicorn18000.log 2>&1 < /dev/null &
disown
for i in $(seq 1 40); do
  sleep 3
  c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$c" = "200" ] && { echo "18000 OK ($((i*3))s)"; break; }
done
echo "timeout threshold in effect:"
curl -s --max-time 10 http://127.0.0.1:18000/api/v1/health >/dev/null && echo "  (config loaded at startup)"
bash ~/uai/src/scripts/deploy_acceptance_gate.sh 2>&1 | tail -14
