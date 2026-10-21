"""A.81：**DICOM 结构感知层** —— 让下游按"真实数据结构"处理，而不是按假设。

**为什么需要**（A.80 实测 580 个文件得出的结论）：
我们的技术栈原本假设的是"单帧、8 bit、有物理标定、像素干净"的普通图片，
但真实数据是：

| 维度 | 实测 |
|---|---|
| SOP Class | 超声多帧 468 / 二次采集 67 / 超声单帧 45 |
| 帧数 | 单帧 509、**7 帧 71（12.2%）** |
| 位深 | 8 bit 535、**16 bit 45** |
| **超声区域校准** | **580/580 都没有**（`SequenceOfUltrasoundRegions` = 0） |

由此产生三类错位（本模块负责把它们**显式化**，让调用方能做对决定）：
1. **多帧被当单帧**：只取中间帧，cine loop 的时序信息丢失；
2. **二次采集被当原始影像**：屏幕截图带烧录标注 → 直接送分割会污染；
3. **无标定却换算毫米**：`PixelSpacing` 存在 ≠ 有物理标定，把像素当 mm 会给出**假尺寸**。

纪律：本模块全部是**纯函数**（除显式解析函数），不读数据库、不写库、不调模型。
"""

from __future__ import annotations

from typing import Any

#: 超声多帧存储（cine loop 常见）
SOP_US_MULTIFRAME = "1.2.840.10008.5.1.4.1.1.3.1"
#: 超声单帧存储
SOP_US_SINGLEFRAME = "1.2.840.10008.5.1.4.1.1.6.1"
#: 二次采集（屏幕截图/导出图，常带烧录标注）
SOP_SECONDARY_CAPTURE = "1.2.840.10008.5.1.4.1.1.7"


def sop_kind(sop_class_uid: str | None) -> str:
    """SOP Class → 语义类别。纯函数（守卫钉住）。"""
    uid = str(sop_class_uid or "").strip()
    if uid == SOP_US_MULTIFRAME:
        return "us_multiframe"
    if uid == SOP_US_SINGLEFRAME:
        return "us_singleframe"
    if uid == SOP_SECONDARY_CAPTURE:
        return "secondary_capture"
    return "other"


def frame_indices(total_frames: int) -> list[int]:
    """多帧取哪几帧（**全系统唯一口径**）。纯函数（守卫钉住）。

    单帧 → [0]；多帧 → 首/中/末去重（心脏收缩/舒张两端差别最大，只取中间帧会丢信息）。
    """
    n = max(1, int(total_frames or 1))
    if n == 1:
        return [0]
    return sorted({0, n // 2, n - 1})


def calibration_status(has_regions: bool, has_pixel_spacing: bool) -> str:
    """物理标定状态。纯函数（守卫钉住）。

    * `region_calibrated`：有 `SequenceOfUltrasoundRegions`（DICOM 标准里超声物理单位的**唯一**依据）；
    * `pixel_spacing_only`：只有 `PixelSpacing`（**不一定可信**，随深度/缩放变化）；
    * `uncalibrated`：两者都没有。
    """
    if has_regions:
        return "region_calibrated"
    return "pixel_spacing_only" if has_pixel_spacing else "uncalibrated"


def measurement_allowed(status: str) -> bool:
    """是否允许把像素换算成物理单位（mm / mm²）。纯函数（守卫钉住）。

    **只有 `region_calibrated` 才允许**。理由（A.80 实测）：
    本库 580 个文件**全部没有区域校准**，`PixelSpacing` 有的是标称值、
    有的与实际缩放不符 —— 拿它乘出 mm² 会给医生一个**看起来很准的假数字**。

    影响：报告不再输出 mm²，改为只报像素面积 + 明确标注"未标定"。
    这比"猜一个数"更符合医疗文书要求（宁可少一个数，不可给错一个数）。
    """
    return status == "region_calibrated"


def structure_of(ds: Any) -> dict:
    """从 pydicom Dataset 汇总结构要素（供探针与管线判断共用）。纯函数（可传替身）。

    返回键：`sop_kind` / `frames` / `bits_stored` / `photometric` / `calibration`
    / `has_pixel_spacing` / `burned_in`（二次采集 → 可能有烧录标注）。
    """
    frames_raw = getattr(ds, "NumberOfFrames", 1)
    try:
        frames = max(1, int(frames_raw))
    except (TypeError, ValueError):  # silent-ok: 非法帧数按单帧，结果已写入返回值的 frames
        frames = 1
    regions = getattr(ds, "SequenceOfUltrasoundRegions", None)
    has_regions = bool(regions) if regions is not None else False
    has_spacing = getattr(ds, "PixelSpacing", None) is not None
    kind = sop_kind(getattr(ds, "SOPClassUID", ""))
    return {
        "sop_kind": kind,
        "frames": frames,
        "bits_stored": int(getattr(ds, "BitsStored", 0) or 0),
        "photometric": str(getattr(ds, "PhotometricInterpretation", "") or ""),
        "calibration": calibration_status(has_regions, has_spacing),
        "has_pixel_spacing": has_spacing,
        # 二次采集是"屏幕截图"：尺寸/测量类信息可能已烧进像素，分割会把它当病灶
        "burned_in": kind == "secondary_capture",
    }


def segmentation_allowed(structure: dict) -> tuple[bool, str]:
    """该文件是否适合送结节分割。纯函数（守卫钉住）。

    * 二次采集（屏幕截图）→ 拒绝：烧录的标注/标尺会被误判成病灶；
    * 其余允许（分割本身在像素域做，不需要物理标定）。
    """
    if structure.get("burned_in"):
        return False, "secondary_capture_burned_in"
    if not structure.get("frames"):
        return False, "no_frames"
    return True, ""


__all__ = [
    "SOP_SECONDARY_CAPTURE",
    "SOP_US_MULTIFRAME",
    "SOP_US_SINGLEFRAME",
    "calibration_status",
    "frame_indices",
    "measurement_allowed",
    "segmentation_allowed",
    "sop_kind",
    "structure_of",
]
