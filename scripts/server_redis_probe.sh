#!/usr/bin/env bash
# 探测"应用实际使用的 Redis URL" → 重启 API → 观察 lifespan 是否完成、健康检查是否 200。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
ENVF=$ROOT/.env
LOG=$ROOT/logs/api.log
cd "$SRC" || exit 1
set -a; . "$ENVF"; set +a

echo "=== A. 应用配置里实际生效的 Redis URL（脱敏）与连接能力 ==="
"$PY" - <<'PY'
import asyncio

from app.config.settings import settings
from app.config.redis import RedisPool


def mask(url: str) -> str:
    # 只显示 "scheme://***(有口令)@host:port/db"，绝不回显口令
    if "@" in url:
        head, tail = url.split("@", 1)
        scheme = head.split("://", 1)[0]
        return f"{scheme}://***@{tail}"
    return url


async def main() -> None:
    print("[settings] redis_url =", mask(settings.redis_url))
    client = await RedisPool.get_client()
    print("[pool] get_client ->", type(client).__name__ if client is not None else "None")
    if client is not None:
        try:
            print("[pool] ping ->", await client.ping())
        except Exception as exc:
            print("[pool] ping 失败:", type(exc).__name__, str(exc)[:90])


asyncio.run(main())
PY
echo "probe_rc=$?"

echo
echo "=== B. 修 infra_health 的另一处硬编码默认（176 行；先备份，失败可回滚）==="
cp "$SRC/app/observability/infra_health.py" "$SRC/app/observability/infra_health.py.bak"
sed -i '0,/    redis_url: str = "redis:\/\/localhost:6379",/s//    redis_url: str | None = None,/' "$SRC/app/observability/infra_health.py"
grep -n 'redis_url' "$SRC/app/observability/infra_health.py" | head -8

echo
echo "=== C. 重启 API，最多等 240s 观察是否开始服务 ==="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 2
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info > "$LOG" 2>&1 &
echo "pid=$!"
for i in $(seq 1 24); do
  sleep 10
  code=$(curl -s -o /tmp/hc.out -m 5 -w '%{http_code}' http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  echo "  t=$((i*10))s  /api/v1/health -> http=$code"
  if [ "$code" = "200" ]; then break; fi
done

echo
echo "=== D. 日志要点 ==="
grep -nE 'startup|lifespan|started|error|Error|refus|unhealthy' "$LOG" | tail -18

echo
echo "=== E. 健康响应体 ==="
curl -s -m 10 http://127.0.0.1:18000/api/v1/health | head -c 400; echo

echo
echo "=== F. 若启动被改坏则回滚 infra_health 改动 ==="
if grep -qE 'TypeError|NameError' "$LOG"; then
  echo "日志出现类型/名称错误 -> 回滚 infra_health.py"
  mv "$SRC/app/observability/infra_health.py.bak" "$SRC/app/observability/infra_health.py"
  pkill -f 'uvicorn app.main:app' 2>/dev/null || true
  sleep 2
  set -a; . "$ENVF"; set +a
  nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info > "$LOG" 2>&1 &
  sleep 20
  echo "回滚后日志尾部:"; tail -6 "$LOG"
else
  echo "无类型错误，保留改动（备份文件保留在 infra_health.py.bak）"
fi
echo "REDIS_PROBE_DONE"
