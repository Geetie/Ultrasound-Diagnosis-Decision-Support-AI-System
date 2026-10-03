#!/usr/bin/env bash
# ①端到端演练：开旁路 → 重启 → 跑演练 → 关旁路 → 重启（不留开关痕）
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
export PYTHONPATH=~/uai/src
LOG=/tmp/_drill_e2e.log
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

restart() {
  pkill -9 -f "uvicorn app.main:app --host 0.0.0.0 --port 18000" 2>/dev/null || true
  sleep 3
  cd ~/uai/src
  export CUDA_VISIBLE_DEVICES=0
  setsid nohup ~/uai/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18000 \
    --log-level warning > /tmp/uvicorn18000.log 2>&1 < /dev/null &
  disown
  for i in $(seq 1 40); do
    sleep 3
    c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:18000/api/v1/health 2>/dev/null)
    [ "$c" = "200" ] && { say "  restarted OK ($((i*3))s)"; return 0; }
  done
  say "  RESTART_FAILED"
  tail -5 /tmp/uvicorn18000.log
  return 1
}

say "=== 0) 找一张 TN3K 测试图 ==="
IMG=$(ls ~/uai/data/tn3k_dataset/test-image/* 2>/dev/null | head -1)
[ -z "$IMG" ] && IMG=$(find ~/uai/data/tn3k_dataset -type f -path "*test-image*" 2>/dev/null | head -1)
say "  using: ${IMG:-NONE}"
[ -z "$IMG" ] && exit 2

say "=== 1) 开旁路（env 注入，不改仓库默认）==="
say "  NODULE_CONTOUR_IN_PIPELINE_ENABLED=1"

say "=== 2) 带旁路重启 ==="
export NODULE_CONTOUR_IN_PIPELINE_ENABLED=1
restart || exit 2
grep -n "nodule_contour_in_pipeline_enabled" ~/uai/src/app/config/settings.py | head -1 | tee -a "$LOG"

say "=== 3) 跑演练 ==="
IMG_PATH="$IMG" ~/uai/venv/bin/python ~/uai/src/scripts/deploy_drill_e2e.py 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
say "  drill rc=$RC"

say "=== 4) 查索引表里有没有行（验证 dicom_images 真写入）==="
~/uai/venv/bin/python - <<'PY' 2>&1 | tee -a "$LOG"
import asyncio, os, sys
sys.path.insert(0, os.path.expanduser("~/uai/src"))
from sqlalchemy import text
from app.config.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        n = (await db.execute(text("select count(*) from dicom_images"))).scalar()
        print("  dicom_images rows:", n)
        rows = (await db.execute(text(
            "select image_id, session_id, modality, width, height, frame_count "
            "from dicom_images order by id desc limit 3"))).all()
        for r in rows:
            print("   ", r)
asyncio.run(main())
PY

say "=== 5) 关旁路并重启（恢复默认关闭）==="
unset NODULE_CONTOUR_IN_PIPELINE_ENABLED
restart || exit 2
say "  旁路已恢复默认关闭"

say "DONE rc=$RC"
exit $RC
