"""A.80：**DICOM 结构研究**（只读）——580 个文件到底是什么格式、是否多帧。

**为什么必须查**：影像向量建库时踩到"ULC 不吃 DICOM、只吃图片"（A.79）。
要判断"取中间帧"是否够、是否该取多帧平均、是否存在多帧 cine loop，
必须先把**真实的数据结构**统计出来，而不是按经验假设。

统计项（逐文件读头，不解码像素，速度快）：
* `SOPClassUID` / `Modality` / `TransferSyntaxUID`
* `Rows`×`Columns` / `NumberOfFrames`（多帧判定）/ `PhotometricInterpretation`
* `BitsAllocated` / `BitsStored` / `PixelRepresentation`
* `PixelSpacing` / `WindowCenter` / `WindowWidth`
* 超声专有：`SequenceOfUltrasoundRegions` 的条目数（区域校准信息）
* 文件大小、以及"数据库 `frame_count` 是否与实际一致"（数据完整性）

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p59_dicom_structure.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import statistics
import sys
from collections import Counter

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def summarize_file(ds) -> dict:
    """从 pydicom Dataset 抽出结构要素（纯函数，守卫钉住）。"""
    frames_raw = getattr(ds, "NumberOfFrames", 1)
    try:
        frames = int(frames_raw)
    except (TypeError, ValueError):
        frames = 1
    regions = getattr(ds, "SequenceOfUltrasoundRegions", None)
    return {
        "sop_class": str(getattr(ds, "SOPClassUID", "")),
        "modality": str(getattr(ds, "Modality", "")),
        "transfer_syntax": str(getattr(ds.file_meta, "TransferSyntaxUID", ""))
        if getattr(ds, "file_meta", None)
        else "",
        "rows": int(getattr(ds, "Rows", 0) or 0),
        "cols": int(getattr(ds, "Columns", 0) or 0),
        "frames": frames,
        "photometric": str(getattr(ds, "PhotometricInterpretation", "")),
        "bits_allocated": int(getattr(ds, "BitsAllocated", 0) or 0),
        "bits_stored": int(getattr(ds, "BitsStored", 0) or 0),
        "pixel_representation": int(getattr(ds, "PixelRepresentation", 0) or 0),
        "has_pixel_spacing": bool(getattr(ds, "PixelSpacing", None)),
        "window_center": bool(getattr(ds, "WindowCenter", None)),
        "window_width": bool(getattr(ds, "WindowWidth", None)),
        "us_regions": len(regions) if regions is not None else 0,
        "manufacturer": str(getattr(ds, "Manufacturer", ""))[:40],
        # `in ds` 只对 pydicom Dataset 有效；用 hasattr 兼容测试替身与 stop_before_pixels
        "has_pixel_data": hasattr(ds, "PixelData") or hasattr(ds, "FloatPixelData"),
    }


async def _rows(limit: int) -> list[dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DicomImage

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DicomImage.image_id,
                    DicomImage.storage_path,
                    DicomImage.frame_count,
                    DicomImage.width,
                    DicomImage.height,
                    DicomImage.size_bytes,
                ).order_by(DicomImage.created_at)
            )
        ).all()
    out = [
        {
            "image_id": str(r[0]),
            "storage_path": str(r[1]),
            "db_frames": int(r[2] or 0),
            "db_w": int(r[3] or 0),
            "db_h": int(r[4] or 0),
            "db_bytes": int(r[5] or 0),
        }
        for r in rows
    ]
    return out[:limit] if limit else out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="只看前 N 个（冒烟）")
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/dicom_structure.json"))
    args = ap.parse_args()

    import pydicom

    db_rows = asyncio.run(_rows(args.limit))
    records: list[dict] = []
    parse_fail = 0
    mismatch = {"frames": 0, "size": 0}

    for row in db_rows:
        path = pathlib.Path(row["storage_path"])
        if not path.is_file():
            parse_fail += 1
            continue
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
        except Exception:
            parse_fail += 1
            continue
        info = summarize_file(ds)
        info["image_id"] = row["image_id"]
        info["file_bytes"] = path.stat().st_size
        info["db_frames"] = row["db_frames"]
        info["db_bytes"] = row["db_bytes"]
        if info["frames"] != max(1, row["db_frames"]):
            mismatch["frames"] += 1
        if row["db_bytes"] and abs(info["file_bytes"] - row["db_bytes"]) > 1024:
            mismatch["size"] += 1
        records.append(info)

    n = len(records)
    if not n:
        print("[FATAL] 一个都没解析成功")
        return 2

    def dist(key: str, top: int = 6) -> dict:
        return dict(Counter(str(r[key]) for r in records).most_common(top))

    frames_hist = Counter(r["frames"] for r in records)
    sizes = [r["file_bytes"] for r in records]
    report = {
        "probe": "p59_dicom_structure",
        "readonly": True,
        "n_files": n,
        "parse_failed": parse_fail,
        "db_inconsistency": mismatch,
        "sop_class": dist("sop_class"),
        "modality": dist("modality"),
        "transfer_syntax": dist("transfer_syntax"),
        "photometric": dist("photometric"),
        "bits": dist("bits_stored"),
        "pixel_representation": dist("pixel_representation"),
        "frames_hist": dict(sorted(frames_hist.items())),
        "multi_frame_ratio": round(
            sum(c for f, c in frames_hist.items() if f > 1) / n, 4
        ),
        "us_regions_hist": dict(sorted(Counter(r["us_regions"] for r in records).items())),
        "rows_cols_top": dist("rows", 4),
        "has_pixel_spacing": sum(1 for r in records if r["has_pixel_spacing"]),
        "has_window": sum(1 for r in records if r["window_center"]),
        # ⚠️ 头是 `stop_before_pixels=True` 读的 → PixelData 一定不存在，
        # 这个字段只对"解码路径"有意义，这里不聚合，避免给出误导性数字。
        "pixel_data_note": "头部读取用 stop_before_pixels=True，未加载像素",
        "file_bytes_median": int(statistics.median(sizes)),
        "file_bytes_max": max(sizes),
        "manufacturer": dist("manufacturer"),
    }
    print(f"解析 {n} 个文件（失败 {parse_fail}）")
    print(f"  SOP Class：{report['sop_class']}")
    print(f"  传输语法：{report['transfer_syntax']}")
    print(f"  帧数分布：{report['frames_hist']}  → **多帧占比 {report['multi_frame_ratio']}**")
    print(f"  位深：{report['bits']}；光度解释：{report['photometric']}")
    print(f"  超声区域校准条目：{report['us_regions_hist']}")
    print(f"  有 PixelSpacing {report['has_pixel_spacing']}/{n}、有窗宽窗位 {report['has_window']}/{n}")
    print(f"  文件大小中位 {report['file_bytes_median']}B、最大 {report['file_bytes_max']}B")
    print(f"  DB 与实际不一致：帧数 {mismatch['frames']} 个、字节数 {mismatch['size']} 个")

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**report, "records": records}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
