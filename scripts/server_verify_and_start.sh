#!/usr/bin/env bash
# 验证 ⑤ 修复（应用侧 engine 可连库）→ stamp alembic 版本 → 启动 API → 健康检查。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
LOG=$ROOT/logs/api.log
DBURL=$(grep '^DATABASE_URL' "$ROOT/.env" | cut -d= -f2-)
REDISURL=$(grep '^REDIS_URL' "$ROOT/.env" | cut -d= -f2-)

echo "=== A. 应用侧 engine 能否连库（⑤ 的验证）==="
cd "$SRC" || exit 1
DATABASE_URL="$DBURL" REDIS_URL="$REDISURL" "$PY" - <<'PY'
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from sqlalchemy import text

from app.config.database import async_engine


async def main() -> None:
    async with async_engine.connect() as conn:
        value = (await conn.execute(text("select 1"))).scalar()
    print("[app-engine] select 1 ->", value)
    await async_engine.dispose()


asyncio.run(main())
PY
echo "app_engine_rc=$?"

echo
echo "=== B. stamp alembic 到 head（结构由模型建出，这里只对齐版本状态）==="
DATABASE_URL="$DBURL" "$PY" -m alembic stamp head 2>&1 | tail -3
DATABASE_URL="$DBURL" "$PY" -m alembic current 2>&1 | tail -2

echo
echo "=== C. 启动 API（127.0.0.1:18000，后台）==="
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 1
cd "$SRC" || exit 1
DATABASE_URL="$DBURL" REDIS_URL="$REDISURL" \
  nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --log-level info \
  > "$LOG" 2>&1 &
echo "uvicorn pid=$!  日志=$LOG"
sleep 25

echo "=== D. 启动日志尾部（含失败原因）==="
tail -22 "$LOG"

echo
echo "=== E. 健康检查 ==="
for path in /api/v1/health /api/v1/health/ready /health; do
  code=$(curl -s -o /tmp/hc.out -m 8 -w '%{http_code}' "http://127.0.0.1:18000$path" 2>/dev/null)
  echo "$path -> http=$code  $(head -c 160 /tmp/hc.out 2>/dev/null)"
done

echo
echo "=== F. 端口与进程 ==="
ss -ltn 2>/dev/null | grep 18000 || echo "(未监听 18000)"
pgrep -af 'uvicorn app.main:app' | head -2
echo "VERIFY_START_DONE"
