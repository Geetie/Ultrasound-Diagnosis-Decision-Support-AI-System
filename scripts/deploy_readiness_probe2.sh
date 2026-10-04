#!/usr/bin/env bash
# 交付就绪度盘点 2：签名链 / 并发 / 前端静态资源 / 遗留开关
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
say() { echo "[$(date +%H:%M:%S)] $*"; }

say "=== 1) 关键开关现状（交付面）==="
grep -nE "nodule_contour_in_pipeline_enabled|attribute_hints_enabled|graph_fusion_enabled|vlm_ocr_enabled" \
  ~/uai/src/app/config/settings.py | sed 's/^/  /'
say "  -- 进程实际环境 --"
PID=$(pgrep -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" | head -1)
tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep -E "NODULE|ATTRIBUTE|GRAPH|VLM" | sed 's/^/  /' || echo "  （无相关 env，用 settings 默认）"

say "=== 2) 前端静态资源（能否给医生用）==="
for ep in / /docs /openapi.json; do
  printf "  %-14s %s\n" "$ep" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:18000${ep}")"
done
curl -s --max-time 10 http://127.0.0.1:18000/ 2>/dev/null | head -c 300 | sed 's/^/  /'
echo ""

say "=== 3) 静态前端目录 ==="
ls -d ~/uai/src/frontend/dist 2>/dev/null && du -sh ~/uai/src/frontend/dist 2>/dev/null | sed 's/^/  /' || echo "  NO frontend/dist"
ls ~/uai/src/static 2>/dev/null | head -5 | sed 's/^/  /' || echo "  NO static/"

say "=== 4) 数据库迁移状态 ==="
PYTHONPATH=~/uai/src ~/uai/venv/bin/alembic current 2>&1 | tail -2 | sed 's/^/  /'
PYTHONPATH=~/uai/src ~/uai/venv/bin/alembic heads 2>&1 | tail -2 | sed 's/^/  /'

say "=== 5) 知识库规模 ==="
PYTHONPATH=~/uai/src ~/uai/venv/bin/python - <<'PY' 2>&1 | tail -6 | sed 's/^/  /'
import asyncio, os, sys
sys.path.insert(0, os.path.expanduser("~/uai/src"))
from sqlalchemy import text
from app.config.database import AsyncSessionLocal

async def m():
    async with AsyncSessionLocal() as db:
        for t in ("knowledge_chunks", "knowledge_base", "dicom_images", "diagnosis_results"):
            try:
                n = (await db.execute(text(f"select count(*) from {t}"))).scalar()
                print(f"{t}: {n}")
            except Exception as e:
                print(f"{t}: ERR {type(e).__name__}")
asyncio.run(m())
PY

say "=== 6) Redis / 会话存储 ==="
PYTHONPATH=~/uai/src ~/uai/venv/bin/python - <<'PY' 2>&1 | tail -4 | sed 's/^/  /'
import asyncio, os, sys
sys.path.insert(0, os.path.expanduser("~/uai/src"))
try:
    import redis.asyncio as aioredis
    from app.config.settings import settings
    url = settings.redis_url if hasattr(settings, "redis_url") else "redis://localhost:6379/0"
    async def m():
        r = aioredis.from_url(url)
        print("redis ping:", await r.ping())
        info = await r.info("memory")
        print("used_memory_human:", info.get("used_memory_human"))
    asyncio.run(m())
except Exception as e:
    print("redis check failed:", type(e).__name__, e)
PY
