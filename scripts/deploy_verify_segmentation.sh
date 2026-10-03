#!/usr/bin/env bash
# ①分割接主链路 · 真机验证（2026-10-04，修正版）
# 教训钉子：①主服务健康端点是 /api/v1/health（不是 /health）
#          ②启动必须 source ~/uai/.env（不在 src/ 下），否则 HSM 密钥缺失直接拒启
#          ③alembic 需要 PYTHONPATH=~/uai/src
set -u
cd ~/uai/src
set -a; source ~/uai/.env; set +a
LOG=/tmp/_seg_verify.log
: > "$LOG"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

say "=== 0. 迁移 0017（dicom_images）==="
PYTHONPATH=~/uai/src ~/uai/venv/bin/alembic current 2>&1 | tail -2 | tee -a "$LOG"
PYTHONPATH=~/uai/src ~/uai/venv/bin/alembic upgrade head 2>&1 | tail -2 | tee -a "$LOG"

say "=== 1. 表与列核对 ==="
PYTHONPATH=~/uai/src ~/uai/venv/bin/python - <<'PY' 2>&1 | tee -a "$LOG"
import asyncio, os, sys
sys.path.insert(0, os.path.expanduser("~/uai/src"))
from sqlalchemy import text
from app.config.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        cols = (await db.execute(text(
            "select column_name from information_schema.columns "
            "where table_name='dicom_images' order by ordinal_position"))).all()
        print("dicom_images columns:", [c[0] for c in cols])
        n = (await db.execute(text("select count(*) from dicom_images"))).scalar()
        print("rows:", n)
asyncio.run(main())
PY

say "=== 2. 主服务健康（/api/v1/health）==="
curl -s -o /dev/null -w "health -> %{http_code}\n" --max-time 10 http://127.0.0.1:18000/api/v1/health

say "=== 3. 分割能力矩阵 ==="
curl -s --max-time 15 http://127.0.0.1:18000/api/v1/segmentation/organs \
  | ~/uai/venv/bin/python -c "import json,sys; d=json.load(sys.stdin)['organs']; [print(f\"  {k:9} {v['status']:16} dice={v['metrics'].get('dice')} reason={v['unavailable_reason']}\") for k,v in sorted(d.items())]" 2>&1 | tee -a "$LOG"

say "=== 4. health?organ=（多器官实例各自加载）==="
for o in thyroid cardiac liver; do
  printf "  %-9s " "$o"
  curl -s --max-time 90 "http://127.0.0.1:18000/api/v1/segmentation/health?organ=$o" \
    | head -c 260; echo ""
done

say "=== 5. 未注册器官 → 404 ==="
curl -s -o /dev/null -w "  pancreas -> %{http_code}\n" --max-time 20 \
  -X POST "http://127.0.0.1:18000/api/v1/segmentation/organ/pancreas/nodule" -F "file=@/dev/null;filename=x.png"

say "=== 6. 中文护栏回归（属性提示应拒中文）==="
curl -s --max-time 30 -X POST http://127.0.0.1:18000/api/v1/attributes/hints \
  -H "Content-Type: application/json" \
  -d '{"image_path":"/tmp/uai_nodata.jpg","text":"甲状腺低回声结节"}' | head -c 260; echo ""

say "=== 7. 六端点复验 ==="
for ep in /api/v1/health /api/v1/diagnosis/request /api/v1/attributes/health /api/v1/imaging/dicom/upload /api/v1/report/templates /api/v1/segmentation/organs; do
  printf "  %-40s %s\n" "$ep" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:18000${ep}")"
done

say "=== 8. 旁路默认关闭确认 ==="
grep -n "nodule_contour_in_pipeline_enabled: bool" ~/uai/src/app/config/settings.py | tee -a "$LOG"

say "DONE"
