"""医生测量/标注的随影像存档（A.40 标注持久化最小闭环）。

存储选型（与 `dicom_storage.path_for_id` 同款确定性推导，**无表无迁移**）：
`{dicom_storage_path}/annotations/{image_id}.json` —— 标注 JSON 跟随影像文件
存档，local backend 下随 DICOM 目录一起备份/迁移；云存储后端接入时把此目录
纳入同策略即可。不做数据库表的理由：标注依附于 image_id 一对一、无查询聚合
需求（按 image_id 取回是唯一访问模式），JSON 文件是最低复杂度的持久化。

写入原子化（tmp + rename），大小上限 256KB —— 标注是几十个坐标点的 JSON，
上限纯粹是防滥用。医生可写：测量是医生工作产物，标注归属登录用户，
权限与影像上传同级。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.v1.errors import (
    BadRequestException,
    NotFoundException,
    PayloadTooLargeException,
)
from app.config.settings import settings
from app.dependencies import get_audit_service
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["annotations"])

STORAGE_ID_PATTERN = re.compile(r"^(?:img|frame)_(\d{8})_[0-9a-f]{8}$")
MAX_ANNOTATIONS_BYTES = 256 * 1024


def _annotation_path(image_id: str) -> Path | None:
    """标注 JSON 路径；image_id 非法返回 None（与 dicom_storage 同口径防路径穿越）。"""
    if not STORAGE_ID_PATTERN.match(image_id):
        return None
    base = Path(settings.dicom_storage_path) / "annotations"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{image_id}.json"


class AnnotationRecord(BaseModel):
    image_id: str = Field(pattern=r"^(?:img|frame)_(\d{8})_[0-9a-f]{8}$")
    annotations: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/{image_id}/annotations")
async def get_annotations(
    image_id: str,
    current_user: dict | None = Depends(get_current_active_user),
) -> dict[str, Any]:
    path = _annotation_path(image_id)
    if path is None:
        raise NotFoundException(error_message=f"影像 id 无效：{image_id[:24]}")
    if not path.exists():
        return {"image_id": image_id, "annotations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 档案损坏按空返回 —— 标注恢复失败不该挡影像显示
        logger.warning("get_annotations: 未预期异常，按原逻辑降级", exc_info=True)
        return {"image_id": image_id, "annotations": []}
    return {"image_id": image_id, "annotations": data.get("annotations", [])}


@router.put("/{image_id}/annotations")
async def put_annotations(
    image_id: str,
    record: AnnotationRecord,
    request: Request,
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
) -> dict[str, Any]:
    if record.image_id != image_id:
        raise BadRequestException(error_message="image_id 与路径不一致")
    path = _annotation_path(image_id)
    if path is None:
        raise NotFoundException(error_message=f"影像 id 无效：{image_id[:24]}")
    username = (current_user or {}).get("username", "unknown")
    payload = json.dumps(
        {
            "annotations": record.annotations,
            "updated_by": username,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
    )
    if len(payload.encode("utf-8")) > MAX_ANNOTATIONS_BYTES:
        raise PayloadTooLargeException(
            error_message=f"标注载荷超过 {MAX_ANNOTATIONS_BYTES // 1024}KB 上限"
        )
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)  # 原子替换：写一半的标注档不该被读到
    await audit_service.log_action(
        action="annotations_save",
        actor=username,
        target_id=image_id,
        target_type="dicom",
        detail={"count": len(record.annotations)},
    )
    return {"image_id": image_id, "saved": len(record.annotations), "ts": time.time()}
