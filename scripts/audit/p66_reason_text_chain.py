"""A.86f：验证 `reason_text` 从分割旁路 → 历史接口模型的整条后端链路（只读）。

不做 3 分钟的真请求，直接用同一批真实影像走两个函数：
1. `_safe_nodule_contour`（分割旁路，产出 segmentation dict）
2. `patients._segmentation_summary`（历史接口模型的组装）
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

    from app.api.v1.patients import _segmentation_summary
    from app.config.database import AsyncSessionLocal
    from app.models.database import DicomImage
    from app.services.diagnosis_pipeline import _safe_nodule_contour

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(DicomImage.image_id, DicomImage.session_id)
                .where(DicomImage.image_id.in_(["img_20261003_34b7adf3"]))
            )
        ).all()

    for iid, sid in rows:
        req = SimpleNamespace(
            request_id="probe-chain", session_id=str(sid), image_ids=[str(iid)], organ="thyroid"
        )
        seg = await _safe_nodule_contour(req)
        print(f"\n[1] 分割旁路产出：{json.dumps({k: seg.get(k) for k in ('available','reason','reason_text')}, ensure_ascii=False)}")

        summary = _segmentation_summary({"segmentation": seg})
        dumped = summary.model_dump() if summary is not None else None
        print(f"[2] 历史接口模型：{json.dumps({'reason': dumped.get('reason'), 'reason_text': dumped.get('reason_text')}, ensure_ascii=False) if dumped else None}")


asyncio.run(main())
