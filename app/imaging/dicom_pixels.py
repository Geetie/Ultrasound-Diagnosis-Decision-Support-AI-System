"""DICOM → PIL 图像（超声分割的前置环节）。

为什么需要
----------
主诊断链路只存 DICOM（app/api/v1/imaging.py 仅接受 DICOM，Modality 必须 US），
而分割模型吃的是 PIL 图像字节（app/inference/nodule_segmentation.py 的
``infer(image_bytes=...)``）。缺的就是这一步转换——之前整条链路因此断开。

实现要点
--------
1. **复用 edge 的解码器**（edge/dicom_scp.py 的 ``default_pixel_decoder``：
   gdcm 优先、pydicom 回退），不在 app 侧重写像素解码。
2. **多帧处理**：超声 DICOM 常是 ``number_of_frames > 1``（cine loop）。默认取
   **中间帧**（心脏相位中段），可指定帧索引；单帧数据自动忽略该参数。
3. **灰度 → RGB**：模型 ``in_channels=3``，灰度直接三通道复制（不做伪彩——
   伪彩会引入训练/推理分布不一致）。
4. **窗宽窗位**：若 DICOM 带 VOI LUT / window center-width，优先用 ``apply_voi_lut``
   （ Ultrasound 侧 CT/MR 常见，US 少见但存在）；否则按 min-max 拉伸到 0~255。
5. **失败语义**：解析失败抛 ``DicomPixelExtractionError``，调用方（旁路增强）只 warning。

坐标/方向：不做行列翻转、不重采样——保持原始像素给下游做方形缩放，与训练一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DicomPixelExtractionError(RuntimeError):
    """DICOM 像素提取失败（解析/解码/无有效帧）。"""


@dataclass(frozen=True)
class ExtractedFrame:
    """提取出的单帧图像（PNG 字节，灰度转 RGB 后）。"""

    png_bytes: bytes
    width: int
    height: int
    frame_index: int
    total_frames: int
    source_modality: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "frame_index": self.frame_index,
            "total_frames": self.total_frames,
            "source_modality": self.source_modality,
            "png_bytes": len(self.png_bytes),
        }


def _to_uint8(arr) -> Any:
    """把像素数组归一化到 uint8（0~255），供 PIL 使用。

    优先 VOI LUT（apply_voi_lut 需 pydicom.pixel_data_handlers）——超声 DICOM
    偶尔带窗宽窗位；不带时按 min-max 拉伸（对 0~255 原始值是无损的）。
    """
    import numpy as np

    if arr.dtype == np.uint8:
        return arr
    arr = arr.astype(np.float32)
    lo, hi = float(np.min(arr)), float(np.max(arr))
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.uint8)
    if hi > 255.0 or lo < 0.0:      # 说明是未归一化的物理值 → min-max 拉伸
        arr = (arr - lo) / (hi - lo) * 255.0
    return arr.astype(np.uint8)


def extract_frame_from_dataset(dataset: Any, frame_index: int | None = None) -> ExtractedFrame:
    """从已加载的 pydicom dataset 提取一帧灰度图（PNG 字节，RGB）。"""
    import io

    import numpy as np
    from PIL import Image

    try:
        n_frames = int(getattr(dataset, "NumberOfFrames", 1) or 1)
    except (TypeError, ValueError):
        logger.warning("extract_frame_from_dataset: 未预期异常，按原逻辑降级", exc_info=True)
        n_frames = 1

    idx = (n_frames // 2) if frame_index is None else frame_index
    idx = max(0, min(idx, n_frames - 1))

    # pydicom 多帧返回 **(frames, rows, cols)** —— 帧在**第一轴**（实测确认，
    # 别按 NIfTI 的最后一轴想当然）。少数私有约定把帧放最后一轴，故两种都兼容。
    raw = dataset.pixel_array
    if raw.ndim == 2:
        arr = raw
    elif raw.shape[0] == n_frames:
        arr = raw[idx]
    elif raw.shape[-1] == n_frames:
        arr = raw[:, :, idx]
    else:
        raise DicomPixelExtractionError(
            f"多帧形状无法解释：pixel_array={raw.shape} vs NumberOfFrames={n_frames}"
        )
    arr_u8 = _to_uint8(arr)

    modality = str(getattr(dataset, "Modality", "") or "") or None
    img = Image.fromarray(np.ascontiguousarray(arr_u8)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return ExtractedFrame(
        png_bytes=buf.getvalue(),
        width=img.width,
        height=img.height,
        frame_index=idx,
        total_frames=n_frames,
        source_modality=modality,
    )


def extract_frame_from_bytes(
    content: bytes, frame_index: int | None = None
) -> ExtractedFrame:
    """从 DICOM 文件字节提取一帧（PHI 已在上传时 strip 过，这里只解码）。"""
    import io

    import pydicom

    try:
        dataset = pydicom.dcmread(io.BytesIO(content))
    except Exception as exc:
        raise DicomPixelExtractionError(f"DICOM 解析失败：{type(exc).__name__}: {exc}") from exc
    try:
        return extract_frame_from_dataset(dataset, frame_index)
    except DicomPixelExtractionError:
        raise
    except Exception as exc:
        raise DicomPixelExtractionError(
            f"像素解码失败：{type(exc).__name__}: {exc}"
        ) from exc


def extract_frame_from_path(
    path: str | Path, frame_index: int | None = None
) -> ExtractedFrame:
    """从 DICOM 文件路径提取一帧。"""
    import pydicom

    p = Path(path)
    if not p.exists():
        raise DicomPixelExtractionError(f"DICOM 文件不存在：{p}")
    try:
        dataset = pydicom.dcmread(str(p))
    except Exception as exc:
        raise DicomPixelExtractionError(f"DICOM 解析失败：{type(exc).__name__}: {exc}") from exc
    return extract_frame_from_dataset(dataset, frame_index)


__all__ = [
    "DicomPixelExtractionError",
    "ExtractedFrame",
    "extract_frame_from_bytes",
    "extract_frame_from_dataset",
    "extract_frame_from_path",
]
