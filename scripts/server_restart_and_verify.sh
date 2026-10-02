#!/bin/bash
# wiring-audit 修复部署：重启主服务 + 新端点核验
UAI="$HOME/uai"
PID=$(ss -ltnp | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$PID" ] && { kill $PID; sleep 4; kill -9 $PID 2>/dev/null; }
cd "$UAI/src" || exit 1
set -a; source "$UAI/.env"; set +a
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 > "$UAI/logs/api.log" 2>&1 &
NEWPID=$!
echo "new pid: $NEWPID"
sleep 10
ps -o lstart= -p $NEWPID 2>/dev/null

echo "== health =="
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/api/v1/health
echo "== new endpoints (401/403/422 = 契约在, 404 = 未上线) =="
# P0-2: nav-token 交换端点（未带 JWT → 401/403 而非 404）
curl -s -o /dev/null -w 'auth/nav-token: %{http_code}\n' -X POST http://127.0.0.1:18000/api/v1/auth/nav-token -H 'Content-Type: application/json' -d '{"session_id":"x"}'
# P0-4: 设备端点（无头 → 401 而非 404）
curl -s -o /dev/null -w 'device/checkin: %{http_code}\n' -X POST http://127.0.0.1:18000/api/v1/admin/device/checkin -H 'Content-Type: application/json' -d '{}'
curl -s -o /dev/null -w 'device/heartbeat: %{http_code}\n' -X POST http://127.0.0.1:18000/api/v1/admin/device/heartbeat -H 'Content-Type: application/json' -d '{}'
# P1-8: 阈值生产者（无 JWT → 401/403 而非 404）
curl -s -o /dev/null -w 'alert/sg-threshold: %{http_code}\n' -X POST http://127.0.0.1:18000/api/v1/admin/alert/sg-threshold -H 'Content-Type: application/json' -d '{"action":"refresh"}'
echo "== other services =="
for p in 8002 8003 8006 8004 8005; do
  printf "%s: " "$p"; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$p/health"
done
printf "9001 sg: "; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9001/health
