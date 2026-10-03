#!/usr/bin/env bash
# 部署 db28ac6（并发审查第二批）到真机并验证。只重启 18000，不碰 8002/8003/8006/9001。
set -u
TAR=/tmp/uai_deploy.tar
SRC=~/uai/src
LOG=~/uai/logs/api.log

echo "=== 1. 部署（tar -xf，保留 soft_hsm_enabled_at.txt）==="
ls -la "$TAR" || { echo "MISSING_TAR"; exit 2; }
tar -xf "$TAR" -C "$SRC" --exclude=soft_hsm_enabled_at.txt && echo "EXTRACT_OK"
cd "$SRC"
grep -c "resolve_rate_limit_config" app/config/rate_limit.py && echo "code-present"
grep -c "enforce_login_rate_limit" app/api/v1/auth.py && echo "auth-present"

echo "=== 2. 重启 18000 ==="
OLD=$(ss -ltnp 2>/dev/null | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$OLD" ]; then kill "$OLD"; sleep 4; kill -9 "$OLD" 2>/dev/null; fi
sleep 2
set -a; source ~/uai/.env; set +a
nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 > "$LOG" 2>&1 &
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  [ "$code" = "200" ] && { echo "api-ready(${i}s)"; break; }
  sleep 1
done

echo "=== 3. 六端点复验 ==="
for p in 18000 8002 8003 8006 8004 9001; do
  c=$(curl -s -o /dev/null -w '%{http_code}' -m 4 "http://127.0.0.1:${p}/health" 2>/dev/null)
  [ "$p" = "18000" ] && c=$(curl -s -o /dev/null -w '%{http_code}' -m 4 "http://127.0.0.1:${p}/api/v1/health" 2>/dev/null)
  echo "PORT_${p}=${c}"
done

echo "=== 4. 登录双维度限流（同名用户连打 8 次，期望出现 429）==="
hit429=0
for i in $(seq 1 8); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -X POST \
    http://127.0.0.1:18000/api/v1/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"__ratelimit_probe__","password":"wrong-pass"}' 2>/dev/null)
  echo -n "attempt${i}=${code} "
  [ "$code" = "429" ] && hit429=1
done
echo ""
echo "login_429_seen=$hit429"

echo "=== 5. 报告链路限流（带路径参数端点，未认证连打 25 次，期望 429）==="
rp429=0
for i in $(seq 1 25); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -X POST \
    "http://127.0.0.1:18000/api/v1/report/probe-dlq-test/sign" 2>/dev/null)
  [ "$code" = "429" ] && rp429=$((rp429+1))
done
echo "report_429_count=${rp429} (of 25)"

echo "=== 6. 多 worker 告警不应误报（单进程启动）==="
if grep -c "worker" "$LOG" 2>/dev/null | head -1; then :; fi
grep -o "multi_worker_subscription_amplified\|多 worker\|worker_count=[0-9]*" "$LOG" 2>/dev/null | sort | uniq -c | head -5
echo "(空=未误报，符合单进程预期)"

echo "=== 7. DLQ 巡检无异常 ==="
if grep -c "dlq_depth_check_failed" "$LOG" 2>/dev/null; then :; else echo "0 (无失败日志)"; fi

echo "=== 8. 分段 seg 端点仍在（我的功能没被这批改动打断）==="
curl -s -m 5 http://127.0.0.1:18000/api/v1/segmentation/health | head -c 200; echo ""
