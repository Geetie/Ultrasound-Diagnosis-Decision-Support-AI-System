#!/usr/bin/env python3
"""A.50.9 E13（DICOM 侧）：真实 C-STORE 推送 —— 本系统影像进真 PACS 对端。

链路：CAMUS 真实帧 → scripts/camus_frame_to_dicom.py 转 DICOM →
pynetdicom C-STORE（标准 Association/SCU）→ UAI_PACS SCP 归档 →
独立读回归档文件断言 SOPInstanceUID/像素一致。

运行（真机）：~/uai/venv/bin/python scripts/audit/p13_dicom_cstore.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _audit_common import check, summary
from pydicom import dcmread
from pydicom.dataset import Dataset  # noqa: F401
from pynetdicom import AE
from pynetdicom.presentation import build_context

STORE = Path.home() / "uai" / "pacs_store"


def convert_frames(n: int = 2) -> list[Path]:
    """CAMUS 真实帧 → DICOM（复用走查同一转换脚本，--out 是前缀）。"""
    out_dir = Path("/tmp/e13_dicom")
    out_dir.mkdir(exist_ok=True)
    script = Path.home() / "uai" / "src" / "scripts" / "camus_frame_to_dicom.py"
    subprocess.run(
        [sys.executable, str(script), "--out", str(out_dir / "camus_e13.dcm"),
         "--count", str(n), "--patient", "CAMUS-E13"],
        check=True, capture_output=True,
    )
    return sorted(out_dir.glob("camus_e13_*.dcm"))


def c_store(path: Path) -> tuple[int, str]:
    """C-STORE 单文件（标准 SCU Association）。"""
    ds = dcmread(str(path))
    ae = AE(ae_title=b"UAI_E13_SCU")
    ae.requested_contexts = [build_context(ds.SOPClassUID)]
    assoc = ae.associate("127.0.0.1", 11112, ae_title=b"UAI_PACS")
    if not assoc.is_established:
        return -1, "association rejected"
    result = assoc.send_c_store(ds)
    assoc.release()
    # pynetdicom 3.x：返回 Dataset（含 (0000,0900) Status 元素），int 视版本
    status = int(result.get("Status", 0xFFFF)) if isinstance(result, Dataset) else int(result)
    # 0x0000=Success；0x0001/0xB000 等为 Warning 级（Coercion）——数据已存档
    if status & 0xF000 in (0xA000, 0xC000):
        return status, "store failed"
    return status, str(ds.SOPInstanceUID)


def main() -> int:
    paths = convert_frames(2)
    check("CAMUS 真实帧转 DICOM", len(paths) == 2 and all(p.stat().st_size > 0 for p in paths),
          f"{[p.name for p in paths]}")
    for p in paths:
        status, sop = c_store(p)
        check(f"C-STORE {p.name}", status == 0, f"status={status} sop={sop[:30]}")
        archived = STORE / f"{sop}.dcm"
        ok = archived.exists() and archived.stat().st_size > 0
        check(f"PACS 归档可读 {p.name}", ok,
              f"archived={archived.stat().st_size if archived.exists() else 0}B")
        if ok:
            back = dcmread(str(archived))
            check(f"归档内容与源一致 {p.name}",
                  str(back.SOPInstanceUID) == sop and "PixelData" in back,
                  f"rows={back.get('Rows')} cols={back.get('Columns')}")
    return summary("E13 DICOM C-STORE 真实对端")


if __name__ == "__main__":
    sys.exit(main())
