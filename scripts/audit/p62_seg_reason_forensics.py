"""A.86b 取证：分割为什么"未做"——把 findings.segmentation 原文打出来（只读）。

`image_not_indexed` 只是 `_safe_nodule_contour` 的**一个**分支；
真实 reason 可能来自：结构准入（二次采集）、模型权重缺失、取图失败、异常兜底。
不打印原文就只能猜。
"""
import asyncio
import json
import os
import pathlib
import sys

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
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(DiagnosisResult)
                .where(DiagnosisResult.image_ids.isnot(None))
                .order_by(DiagnosisResult.created_at.desc())
                .limit(5)
            )
        ).scalars().all()

    for r in rows:
        f = r.findings or {}
        seg = f.get("segmentation")
        sim = f.get("similar_cases")
        print(f"\n== {r.request_id} ({r.organ}) status={r.status} ==")
        print(f"   segmentation = {json.dumps(seg, ensure_ascii=False)[:300]}")
        print(f"   similar_cases= {json.dumps({k: sim.get(k) for k in ('status','count','scope')}, ensure_ascii=False) if isinstance(sim, dict) else sim}")
        tr = (f.get("fusion_result") or {})
        print(f"   正文长度 = {len(str(tr.get('content') or ''))}")


asyncio.run(main())
