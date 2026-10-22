"""A.86c 取证：新上传的影像为什么取不到字节（image_bytes_unavailable）。

对比：10-22 新传的两张（失败） vs 10-14 的旧图（成功），看
`storage_path` / 磁盘是否存在 / 文件大小 / `storage.retrieve()` 返回长度。
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
    from app.models.database import DicomImage
    from app.services.dicom_storage import get_dicom_storage

    print(f"cwd = {os.getcwd()}")
    print(f"settings.dicom_storage_path = {settings.dicom_storage_path!r}")

    targets = ["img_20261022_86c6c100", "img_20261022_711aaf2a", "img_20261022_e072f8c3",
               "img_20261014_88fce652", "img_20261003_34b7adf3"]
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(DicomImage.image_id, DicomImage.storage_path, DicomImage.size_bytes,
                       DicomImage.session_id)
                .where(DicomImage.image_id.in_(targets))
            )
        ).all()

    storage = get_dicom_storage()
    for iid, path, size, sid in rows:
        p = pathlib.Path(str(path))
        exists = p.resolve().is_file() if str(path) else False
        disk = p.stat().st_size if exists else None
        try:
            data = await storage.retrieve(str(path))
            got = len(data or b"")
        except Exception as exc:  # silent-ok: 取图异常本身就是要查的东西，已打印到输出
            got = f"{type(exc).__name__}: {exc}"[:80]
        print(f"\n{iid} (session={sid})")
        print(f"   storage_path = {path}")
        print(f"   size_bytes(DB) = {size}")
        print(f"   磁盘存在 = {exists}  磁盘大小 = {disk}")
        print(f"   storage.retrieve() 返回 = {got}")


asyncio.run(main())
