#!/usr/bin/env bash
# 重启 SafetyGateway + 主服务，并做健康探测（全部只绑 127.0.0.1）
set -u
UAI="$HOME/uai"
SRC="$UAI/src"

# 0) 融合层（主服务）在本部署里是 18000，不是默认 8000：
#    SG 的 fusion probe 默认探 localhost:8000 → 会一直 connection_refused。
if [ -f "$UAI/.env" ] && ! grep -q '^FUSION_LAYER_PORT=' "$UAI/.env"; then
  echo 'FUSION_LAYER_PORT=18000' >> "$UAI/.env"
  echo "env: appended FUSION_LAYER_PORT=18000"
fi

cd "$SRC" || exit 1
set -a
# shellcheck disable=SC1091
. "$UAI/.env"
set +a
export CUDA_VISIBLE_DEVICES=0

pkill -f 'uvicorn safety_gateway.main:app' && echo "sg stopped" || true
pkill -f 'uvicorn app.main:app' && echo "api stopped" || true
sleep 3

echo "===== SG RESTART $(date '+%F %T') =====" >> "$UAI/logs/sg.log"
nohup "$UAI/venv/bin/python" -m uvicorn safety_gateway.main:app \
  --host 127.0.0.1 --port 9001 --log-level info >> "$UAI/logs/sg.log" 2>&1 &
echo "sg pid $!"

echo "===== RESTART $(date '+%F %T') =====" >> "$UAI/logs/api.log"
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app \
  --host 127.0.0.1 --port 18000 --log-level info >> "$UAI/logs/api.log" 2>&1 &
echo "api pid $!"

echo "--- SG 健康 ---"
for i in $(seq 1 20); do
  code=$(curl -s -o /tmp/sg.json -w '%{http_code}' --max-time 4 \
    http://127.0.0.1:9001/health 2>/dev/null || true)
  if [ -n "$code" ] && [ "$code" != "000" ]; then
    echo "sg http=$code (t=$i)"
    head -c 500 /tmp/sg.json
    echo
    break
  fi
  sleep 2
done

echo "--- 主服务健康 ---"
for i in $(seq 1 60); do
  code=$(curl -s -o /tmp/health.json -w '%{http_code}' --max-time 4 \
    http://127.0.0.1:18000/api/v1/health 2>/dev/null || true)
  if [ -n "$code" ] && [ "$code" != "000" ]; then
    echo "api http=$code (t=$i)"
    head -c 700 /tmp/health.json
    echo
    break
  fi
  sleep 3
done

echo
echo "=== 主服务新启动段（错误计数）==="
tr -d '\000' < "$UAI/logs/api.log" > "$UAI/logs/api.clean.log"
start=$(grep -n '===== RESTART' "$UAI/logs/api.clean.log" | tail -1 | cut -d: -f1)
sed -n "${start},\$p" "$UAI/logs/api.clean.log" > /tmp/seg.log
grep -oE '\[(error|critical|warning) +\] +[a-z_]+' /tmp/seg.log | sort | uniq -c | sort -rn
echo "--- 关键行 ---"
grep -aE 'rag_service|pg_health|safety_gateway_health|placeholder|startup complete|metrics server|obs_vm' /tmp/seg.log | tail -20
echo "=== done ==="
