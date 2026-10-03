#!/usr/bin/env bash
# 重启 18000 主服务（加载 segmentation 路由）并探活
set -u
OLD=$(ss -ltnp 2>/dev/null | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$OLD" ]; then
  kill "$OLD"; sleep 4; kill -9 "$OLD" 2>/dev/null
fi
sleep 2
cd ~/uai/src
set -a; source ~/uai/.env; set +a
nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 \
  > ~/uai/logs/api.log 2>&1 &
echo "restart issued"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  if [ "$code" = "200" ]; then echo "api-18000-ready (${i}s)"; break; fi
  sleep 1
done
curl -s -o /dev/null -w "seg_health=%{http_code}\n" -m 5 http://127.0.0.1:18000/api/v1/segmentation/health
curl -s -m 5 http://127.0.0.1:18000/api/v1/segmentation/health
