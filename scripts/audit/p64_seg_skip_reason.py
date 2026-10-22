"""A.86d 取证：这三张"取不到字节"的影像，SOP 类别与分割准入到底如何。"""
import asyncio
import io
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
    import pydicom
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.imaging.dicom_structure import segmentation_allowed, structure_of
    from app.models.database import DicomImage

    targets = ["img_20261022_86c6c100", "img_20261022_711aaf2a",
               "img_20261014_88fce652", "img_20261003_34b7adf3"]
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(DicomImage.image_id, DicomImage.storage_path)
                .where(DicomImage.image_id.in_(targets))
            )
        ).all()

    for iid, path in rows:
        p = pathlib.Path(str(path))
        ds = pydicom.dcmread(io.BytesIO(p.read_bytes()), stop_before_pixels=True, force=True)
        st = structure_of(ds)
        print(f"\n{iid}")
        print(f"   SOPClassUID = {getattr(ds, 'SOPClassUID', '')}")
        print(f"   sop_kind={st['sop_kind']} frames={st['frames']} "
              f"calibration={st['calibration']} burned_in={st['burned_in']}")
        print(f"   segmentation_allowed = {segmentation_allowed(st)}")


asyncio.run(main())
