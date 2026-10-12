#!/usr/bin/env python3
"""p21 — 合规性规范化端到端复验（A.52：把「不合规多帧」喂进**生产 API**）。

针对真机抓到的缺陷：原生多帧「每帧字节数为奇」（8 位 549×389=213561）→ DICOM 偶长度
填充 → 前端 `总长/帧数` 不整除 → 影像黑屏。修法在**入库侧**做 8→16 位重写（零像素改动）。

本探针走生产链路（不是直调函数）：
    上传坏文件 → 取回字节 → 断言①帧数可整除②像素值与原文件**逐位相同**③图像尺寸/帧数不变
    →（可选）发起一次判读，确认分割旁路仍能解帧。

用法（服务器）：
    ~/uai/venv/bin/python p21_odd_frame_normalize.py [--file ~/uai/data/probe/a51_ui_mf7.dcm]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import pathlib
import sys
import time

import httpx
import numpy as np
import pydicom

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _audit_common import BASE, check, login, pw, summary


def _frame_bytes(ds: pydicom.Dataset) -> int:
    return int(ds.Rows) * int(ds.Columns) * (int(ds.BitsAllocated) // 8) * int(
        getattr(ds, "SamplesPerPixel", 1)
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="~/uai/data/probe/a51_ui_mf7.dcm")
    args = ap.parse_args()
    src = pathlib.Path(args.file).expanduser()
    raw = src.read_bytes()
    original = pydicom.dcmread(io.BytesIO(raw), force=True)

    frames = int(getattr(original, "NumberOfFrames", 1) or 1)
    fb = _frame_bytes(original)
    check("P0 前置：样本确为「原生多帧 + 奇帧长」（否则本探针无意义）",
          frames > 1 and fb % 2 == 1, f"frames={frames} frame_bytes={fb}")

    doctor = login("smoke-doctor", pw("smoke_pw"))
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    tok = {"Authorization": f"Bearer {doctor['access_token']}"}
    atok = {"Authorization": f"Bearer {admin['access_token']}"}

    pid = f"probe-p21-{int(time.time())}"
    async with httpx.AsyncClient(timeout=120.0) as c:
        await c.post(f"{BASE}/fhir/Patient", json={"patient_id": pid, "name": "P21", "gender": "unknown"}, headers=tok)
        await c.post(
            f"{BASE}/api/v1/consent",
            json={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"},
            headers=atok,
        )
        r = await c.post(
            f"{BASE}/api/v1/sessions",
            json={"patient_id": pid, "device_id": "probe-p21", "target_organs": ["thyroid"]},
            headers=tok,
        )
        sid = r.json()["session_id"]

        r = await c.post(
            f"{BASE}/api/v1/imaging/dicom/upload",
            files={"file": (src.name, raw, "application/dicom")},
            data={"session_id": sid, "priority": "1"},
            headers=tok,
        )
        check("P1 不合规多帧被接受（规范化不阻断上传）", r.status_code == 200, f"http={r.status_code}")
        if r.status_code != 200:
            return summary("p21 规范化复验")
        image_id = r.json()["image_id"]

        # 回取端点 = GET /api/v1/imaging/dicom/{image_id}（裸 application/dicom 字节），
        # 与前端「服务端副本」显示链路同一条 —— 拿到的就是医生会渲染的那份。
        r = await c.get(f"{BASE}/api/v1/imaging/dicom/{image_id}", headers=tok)
        check("P2 服务端回取字节成功（显示走的就是这份）", r.status_code == 200, f"http={r.status_code}")
        stored = pydicom.dcmread(io.BytesIO(r.content), force=True)

    s_frames = int(getattr(stored, "NumberOfFrames", 1) or 1)
    s_fb = _frame_bytes(stored)
    total = len(stored.PixelData)
    check("P3 回取副本：帧长与总长**可整除**（前端不再报 Invalid frame length）",
          s_fb % 2 == 0 and total % s_frames == 0,
          f"frame_bytes={s_fb}(even={s_fb % 2 == 0}) total={total} %{s_frames}={total % s_frames}")

    check("P4 尺寸/帧数不变（测量口径不受影响）",
          (int(stored.Rows), int(stored.Columns), s_frames)
          == (int(original.Rows), int(original.Columns), frames),
          f"{stored.Rows}x{stored.Columns}x{s_frames} vs {original.Rows}x{original.Columns}x{frames}")

    same = np.array_equal(np.asarray(stored.pixel_array), np.asarray(original.pixel_array))
    check("P5 ⭐ 像素值与原文件**逐位相同**（零改动规范化）", same,
          f"shape={stored.pixel_array.shape} equal={same}")

    check("P6 位深已重写为 16 位（BitsStored 仍 8，值域不变）",
          int(stored.BitsAllocated) == 16 and int(stored.BitsStored) == 8,
          f"BitsAllocated={stored.BitsAllocated} BitsStored={stored.BitsStored}")
    return summary("p21 规范化复验")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
