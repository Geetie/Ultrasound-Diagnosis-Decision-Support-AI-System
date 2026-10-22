"""A.86 取证：走查里分割为何报 image_not_indexed（只读）。

链路：请求行的 image_ids + session_id → `filter_existing_in_session` → 应为非空。
若为空，逐项对比：①请求行的 image_ids 是什么类型/值 ②这些 image_id 在 dicom_images 里的 session_id
③两者是否相等 ④分割旁路开关的真实取值。
"""
import asyncio
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
    from app.config.settings import settings

    # 注意：**请求与结果同表**（`diagnosis_results`，类名 DiagnosisResult）——
    # 没有独立的 DiagnosisRequest 模型（首版写错，ImportError）。
    from app.models.database import DiagnosisResult, DicomImage
    from app.repositories.dicom_image_repository import DicomImageRepository

    print("== 开关 ==")
    for name in dir(settings):
        if "segment" in name and name.startswith("nodule"):
            print(f"   {name} = {getattr(settings, name)!r}")

    async with AsyncSessionLocal() as s:
        # 走查最后一条请求
        # 取**最近一条带影像**的请求（最后一条可能是"仅文字"分支，image_ids 为 None）
        req = (
            await s.execute(
                select(DiagnosisResult)
                .where(DiagnosisResult.image_ids.isnot(None))
                .order_by(DiagnosisResult.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if req is None:
            print("[FATAL] 没有请求行")
            return
        print("\n== 最近一条请求 ==")
        print(f"   request_id   = {req.request_id}")
        print(f"   session_id   = {req.session_id}")
        print(f"   image_ids    = {req.image_ids!r}  (type={type(req.image_ids).__name__})")
        print(f"   organ        = {getattr(req, 'organ', None)}")

        ids = list(req.image_ids or [])
        print(f"   → 解析出的 image_ids（{len(ids)} 条）：{ids[:3]}")

        rows = (
            await s.execute(
                select(DicomImage.image_id, DicomImage.session_id, DicomImage.patient_id,
                       DicomImage.storage_path)
                .where(DicomImage.image_id.in_(ids))
            )
        ).all() if ids else []
        print(f"\n== 库里这些 image_id 的实际归属（{len(rows)}/{len(ids)} 命中）==")
        for iid, sid, _pid, _path in rows:
            same = "✓" if str(sid) == str(req.session_id) else "✗ 会话不一致"
            print(f"   {iid} session={sid} {same}")

        repo = DicomImageRepository(s)
        got = await repo.filter_existing_in_session(str(req.session_id), ids)
        print(f"\n== filter_existing_in_session 结果：{len(got)} 条 ==")

        # 该会话下到底有哪些影像
        in_sess = (
            await s.execute(
                select(DicomImage.image_id, DicomImage.created_at)
                .where(DicomImage.session_id == str(req.session_id))
                .order_by(DicomImage.created_at.desc())
                .limit(5)
            )
        ).all()
        print(f"== 该会话名下影像（最多 5 条）：{[(str(a), str(b)[:19]) for a, b in in_sess]} ==")

        # 请求行自身的时间与影像时间对比（是不是"先提交后上传"）
        print("\n== 时间 ==")
        print(f"   请求 created_at = {getattr(req, 'created_at', None)}")


asyncio.run(main())
