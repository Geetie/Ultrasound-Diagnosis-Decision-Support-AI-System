#!/usr/bin/env bash
# 修 Redis 连接：查清配置来源 → 把 .env 放到项目根（pydantic-settings 原生读取）→ 重启并观察。
#
# 现状：应用 lifespan 已跑通（worker pool / metrics server / VM pusher 都起来了），
# 但 Redis 客户端拿不到连接：`'NoneType' object has no attribute 'blpop'` + socket 超时，
# 说明 RedisPool.get_client() 返回 None（连不上），而**端口/口令是我们自己起的 16379 + requirepass**。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
ENVF=$ROOT/.env
LOG=$ROOT/logs/api.log
cd "$SRC" || exit 1

echo "=== A. 应用侧 Redis 相关配置名与取值方式 ==="
grep -rn 'redis' "$SRC/app/config/settings.py" 2>/dev/null | grep -iE 'url|host|port|password|single_instance' | head -12
echo "--- redis.py 如何取 URL / 失败时返回什么 ---"
grep -rn 'redis_url\|REDIS_GENERAL_URL\|get_client\|def setex\|password' "$SRC/app/config/redis.py" 2>/dev/null | head -16

echo
echo "=== B. 应用进程实际看到的环境变量（脱敏：只显示是否设置与长度）==="
set -a; . "$ENVF"; set +a
for k in REDIS_URL REDIS_SINGLE_INSTANCE REDIS_GENERAL_URL DATABASE_URL USE_SOFT_HSM; do
  v=$(printenv "$k" 2>/dev/null || true)
  if [ -n "$v" ]; then echo "  $k = <已设置，长度 ${#v}>"; else echo "  $k = <未设置>"; fi
done

echo
echo "=== C. 把 .env 复制到项目根（让 pydantic-settings 原生读到），并保留 600 权限 ==="
cp "$ENVF" "$SRC/.env" && chmod 600 "$SRC/.env" && ls -l "$SRC/.env"

echo
echo "=== D. 直接验证应用侧能否连上我们的 Redis（用同一 URL）==="
"$PY" - <<'PY'
import asyncio
import os

import redis.asyncio as aioredis


async def main() -> None:
    url = os.environ.get("REDIS_URL")
    print("[probe] url 前缀:", (url or "")[:14], "...")
    client = aioredis.from_url(url)
    print("[probe] ping ->", await client.ping())
    await client.aclose()


asyncio.run(main())
PY
echo "probe_rc=$?"

echo
echo "=== E. 重启 API 并给足启动时间 ==="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 2
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info > "$LOG" 2>&1 &
echo "pid=$!"
for _ in $(seq 1 12); do
  sleep 10
  if ss -ltn 2>/dev/null | grep -q 18000; then echo "已在第 $((_*10)) 秒监听 18000"; break; fi
done

echo
echo "=== F. 日志：红线为错误 ==="
grep -nE 'error|Error|refus|fail' "$LOG" | tail -12
echo "--- 尾部 ---"
tail -8 "$LOG"

echo
echo "=== G. 健康检查 ==="
for path in /api/v1/health /api/v1/health/hsm /api/v1/health/cache; do
  code=$(curl -s -o /tmp/hc.out -m 10 -w '%{http_code}' "http://127.0.0.1:18000$path" 2>/dev/null)
  echo "$path -> http=$code  $(head -c 200 /tmp/hc.out 2>/dev/null)"
done
ss -ltn 2>/dev/null | grep 18000 || echo "(仍未监听 18000)"
echo "REDIS_FIX_DONE"
