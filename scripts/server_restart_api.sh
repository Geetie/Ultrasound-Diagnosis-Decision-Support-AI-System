#!/bin/bash
# 重启主 API（18000）。并发审查 P2-2：可选的全局并发闸门。
#
# 为什么加这个开关：此前网关只有"逐端点限流"，没有 lifespan 级闸门——未登记的
# 端点与突发洪水只能靠端点自己扛。uvicorn 的 --limit-concurrency 会在**请求入口**
# 统一拒（503），是最后一道兜底。
#
# 怎么定值：本轮阶梯压测（reports/concurrency_perf_report.json）显示
#   50 并发提交时 submit p95 = 0.875s、端到端 p99 = 34.6s、零 429、零错误，
# 即 16 个 worker 槽位 + 队列背压已能吸收 50 并发。留足排队余量后：
#   LIMIT_CONCURRENCY=200 起步，观察 5xx 与 p99 再收紧；0 或不设 = 不启用。
# 生产 .env 里加一行即可生效，不需要改本脚本。
UAI="$HOME/uai"
PID=$(ss -ltnp | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$PID" ] && { kill $PID; sleep 4; kill -9 $PID 2>/dev/null; }
cd "$UAI/src" || exit 1
set -a; source "$UAI/.env"; set +a

LIMIT_CONCURRENCY="${LIMIT_CONCURRENCY:-0}"
EXTRA=""
if [ "$LIMIT_CONCURRENCY" -gt 0 ] 2>/dev/null; then
  EXTRA="--limit-concurrency $LIMIT_CONCURRENCY"
  echo "global concurrency gate: ON ($LIMIT_CONCURRENCY)"
else
  echo "global concurrency gate: OFF (set LIMIT_CONCURRENCY in .env to enable)"
fi

# shellcheck disable=SC2086
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 $EXTRA > "$UAI/logs/api.log" 2>&1 &
echo "new pid: $!"
sleep 10
ps -o lstart= -p $! 2>/dev/null
curl -s http://127.0.0.1:18000/api/v1/health | head -c 300
echo