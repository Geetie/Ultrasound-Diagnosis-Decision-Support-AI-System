"""A.86e 验证：分割未做的原因是否已"说真话"（真机，直接调 `_safe_nodule_contour`）。

两条对照：
* 二次采集（屏幕截图）→ 期望 reason=`secondary_capture_burned_in` + 中文可行动文案；
* 超声多帧（原始件）→ 期望 available=True（分割真跑起来）。
"""
import asyncio
import json
import os
import pathlib
import sys
from types import SimpleNamespace

SRC = pathlib.Path("/mnt/ee16/home/student03/uai/src")
_ENV = pathlib.Path("/mnt/ee16/home/student03/uai/.env")
for line in _ENV.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(SRC))
os.chdir(SRC)


async def main():
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DicomImage
    from app.services.diagnosis_pipeline import _safe_nodule_contour

    wanted = ["img_20261003_34b7adf3", "img_20261014_88fce652"]
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(DicomImage.image_id, DicomImage.session_id)
                .where(DicomImage.image_id.in_(wanted))
            )
        ).all()

    for iid, sid in rows:
        req = SimpleNamespace(
            request_id=f"probe-{iid}", session_id=str(sid), image_ids=[str(iid)], organ="thyroid"
        )
        out = await _safe_nodule_contour(req)
        keep = {k: out.get(k) for k in
                ("available", "reason", "reason_text", "mean_prob", "area_px", "area_mm2", "skipped")}
        print(f"\n{iid}")
        print("   " + json.dumps(keep, ensure_ascii=False)[:420])


asyncio.run(main())
