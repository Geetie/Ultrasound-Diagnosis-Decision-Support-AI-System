#!/usr/bin/env bash
# 修 RedisPool._is_connected：兼容 ping() 返回协程或布尔两种形态 → 重启观察。
#
# 现场证据：
#   [settings] redis_url = redis://***@127.0.0.1:16379/0   （URL 已生效）
#   [pool] get_client -> Redis                              （客户端创建成功）
#   [pool] ping 失败: TypeError object bool can't be used in 'await' expression
# 而 redis.py:44-49 的 `await client.ping()` 被 `except Exception: return False` 吞掉，
# 于是 _ensure_connected 认为连接失效 -> aclose() 并把 _client 置 None ->
# 上层拿到 None，报 `'NoneType' object has no attribute 'blpop'`。
# 修法：把结果按"可等待则 await，否则直接取布尔"处理，两种形态都正确。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
ENVF=$ROOT/.env
LOG=$ROOT/logs/api.log
TARGET=$SRC/app/config/redis.py

echo "=== A. redis 包版本与 ping 的真实形态 ==="
"$PY" -m pip show redis 2>/dev/null | head -2
cd "$SRC" || exit 1
set -a; . "$ENVF"; set +a
"$PY" - <<'PY'
import asyncio
import os
import inspect

import redis.asyncio as aioredis


async def main() -> None:
    client = aioredis.from_url(os.environ["REDIS_URL"])
    raw = client.ping()
    print("[probe] ping() 返回类型:", type(raw).__name__, "| isawaitable:", inspect.isawaitable(raw))
    if inspect.isawaitable(raw):
        print("[probe] awaited ->", await raw)
    else:
        print("[probe] 直接取值 ->", raw)
    await client.aclose()


asyncio.run(main())
PY

echo
echo "=== B. 打补丁（先备份）==="
cp "$TARGET" "$TARGET.bak"
"$PY" - "$TARGET" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
src = path.read_text(encoding="utf-8")
old = """    async def _is_connected(cls, client: redis.Redis) -> bool:
        try:
            await client.ping()
            return True
        except Exception:
            return False"""
new = """    async def _is_connected(cls, client: redis.Redis) -> bool:
        # 2026-09-28 现场部署修复：不同 redis-py 版本/客户端形态下 ping() 可能返回
        # 协程，也可能直接返回布尔。原实现一律 `await`，在后者上抛
        # `TypeError: object bool can't be used in 'await' expression`，而该异常被
        # `except Exception` 吞掉 -> 连接被误判失效 -> 客户端被关闭并置 None ->
        # 上层报 `'NoneType' object has no attribute 'blpop'`，Redis 实际不可用。
        try:
            result = client.ping()
            if hasattr(result, "__await__"):
                result = await result
            return bool(result)
        except Exception:
            return False"""
if old not in src:
    print("[patch] 未匹配到目标代码，未修改")
    sys.exit(1)
path.write_text(src.replace(old, new, 1), encoding="utf-8")
print("[patch] 已应用")
PY
echo "patch_rc=$?"

echo
echo "=== C. 重启并观察最多 150s ==="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 2
nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info > "$LOG" 2>&1 &
echo "pid=$!"
for i in $(seq 1 15); do
  sleep 10
  code=$(curl -s -o /tmp/hc.out -m 5 -w '%{http_code}' http://127.0.0.1:18000/api/v1/health 2>/dev/null)
  echo "  t=$((i*10))s -> http=$code"
  [ "$code" = "200" ] && break
done

echo
echo "=== D. 日志要点（-a：日志含二进制字节，需强制文本）==="
grep -a -nE 'redis_client_created|blpop|ping|startup|lifespan|Ready|error|unhealthy' "$LOG" 2>/dev/null | tail -16
echo "--- 尾部 ---"
tail -c 1200 "$LOG" | tr -d '\000' | tail -10

echo
echo "=== E. 健康响应 ==="
curl -s -m 10 http://127.0.0.1:18000/api/v1/health | head -c 400; echo
ss -ltn 2>/dev/null | grep 18000 || echo "(未监听)"
echo "REDIS_PING_FIX_DONE"
