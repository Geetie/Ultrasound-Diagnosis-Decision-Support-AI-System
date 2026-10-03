"""结节分割 API（甲状腺超声辅助勾画）。

- ``POST /api/v1/segmentation/nodule``：上传超声图 → 返回 PNG 掩膜 + 量化指标
- ``GET  /api/v1/segmentation/health``：权重/设备可用性（不抛异常）

设计原则：能力不可用时**显式 503 + 原因**，绝不返回空掩膜让医生误以为"没结节"。
"""
from __future__ import annotations

import base64

import structlog
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/segmentation", tags=["segmentation"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB


class NoduleSegmentationResponse(BaseModel):
    mask_png_base64: str
    width: int
    height: int
    threshold: float
    lesion_area_ratio: float
    bbox: list[int] | None
    mean_prob: float
    max_prob: float
    latency_ms: float
    device: str
    model_dice_official: float | None


@router.post("/nodule", response_model=NoduleSegmentationResponse)
async def segment_nodule(
    file: UploadFile = File(..., description="超声图像（PNG/JPEG）"),
    threshold: float | None = Query(None, ge=0.0, le=1.0),
) -> NoduleSegmentationResponse:
    """对一张甲状腺超声图做结节分割。"""
    from app.inference.nodule_segmentation import (
        NoduleSegmenter,
        SegmentationUnavailableError,
    )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 20MB 上限")

    try:
        result = await __import__("asyncio").to_thread(
            NoduleSegmenter.get().infer,
            image_bytes=data,
            threshold=threshold,
        )
    except SegmentationUnavailableError as exc:
        logger.warning("nodule_segmentation_unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail=f"结节分割不可用：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.error("nodule_segmentation_failed", error=str(exc), error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail="结节分割执行失败") from exc

    return NoduleSegmentationResponse(
        mask_png_base64=base64.b64encode(result.mask_png).decode("ascii"),
        width=result.width,
        height=result.height,
        threshold=result.threshold,
        lesion_area_ratio=round(result.lesion_area_ratio, 6),
        bbox=list(result.bbox) if result.bbox else None,
        mean_prob=round(result.mean_prob, 6),
        max_prob=round(result.max_prob, 6),
        latency_ms=result.latency_ms,
        device=result.device,
        model_dice_official=result.model_dice_official,
    )


@router.get("/health")
async def segmentation_health() -> dict:
    """分割能力可用性（永远 200，故障体现在 available/reason）。"""
    from app.inference.nodule_segmentation import NoduleSegmenter

    return NoduleSegmenter.get().health()


@router.get("/organs")
async def segmentation_organs() -> dict:
    """多器官分割能力矩阵（哪些器官已训练/待训练/无数据）。"""
    from app.inference.organ_segmentation import list_specs

    return {"organs": list_specs()}
