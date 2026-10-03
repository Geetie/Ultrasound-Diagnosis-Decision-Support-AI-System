"""DICOM 影像索引仓储（dicom_images 表）。

存在的意义：把"哪个 image_id 属于哪个 session、字节在哪"从 audit_logs 的
JSONB 里挖出来变成**可查、可校验、可被管线使用**的一等数据。
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import DicomImage

logger = structlog.get_logger(__name__)


class DicomImageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        image_id: str,
        session_id: str,
        storage_path: str,
        patient_id: str | None = None,
        sop_instance_uid: str | None = None,
        modality: str | None = None,
        width: int | None = None,
        height: int | None = None,
        frame_count: int | None = None,
        size_bytes: int | None = None,
        original_filename: str | None = None,
    ) -> DicomImage:
        row = DicomImage(
            image_id=image_id,
            session_id=session_id,
            patient_id=patient_id,
            storage_path=storage_path,
            sop_instance_uid=sop_instance_uid,
            modality=modality,
            width=width,
            height=height,
            frame_count=frame_count,
            size_bytes=size_bytes,
            original_filename=original_filename,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, image_id: str) -> DicomImage | None:
        return await self.db.scalar(
            select(DicomImage).where(DicomImage.image_id == image_id)
        )

    async def list_by_session(self, session_id: str, limit: int = 50) -> list[DicomImage]:
        """按 session 列出影像（最早优先）——管线"按 session 取图"的入口。"""
        result = await self.db.scalars(
            select(DicomImage)
            .where(DicomImage.session_id == session_id)
            .order_by(DicomImage.created_at.asc())
            .limit(limit)
        )
        return list(result)

    async def filter_existing_in_session(
        self, session_id: str, image_ids: list[str]
    ) -> list[DicomImage]:
        """批量取"属于该 session 且存在"的影像行（保持入参顺序）。"""
        if not image_ids:
            return []
        result = await self.db.scalars(
            select(DicomImage).where(
                DicomImage.session_id == session_id,
                DicomImage.image_id.in_(list(image_ids)),
            )
        )
        by_id = {r.image_id: r for r in result}
        return [by_id[i] for i in image_ids if i in by_id]

    async def missing_from_session(
        self, session_id: str, image_ids: list[str]
    ) -> list[str]:
        """返回"不在该 session 或不存在"的 image_ids（诊断创建时做提示用）。"""
        found = {r.image_id for r in await self.filter_existing_in_session(
            session_id, image_ids
        )}
        return [i for i in (image_ids or []) if i not in found]

    async def storage_paths_for(self, image_ids: list[str]) -> dict[str, str]:
        """image_ids → {image_id: storage_path}（不校验 session）。"""
        if not image_ids:
            return {}
        result = await self.db.scalars(
            select(DicomImage).where(DicomImage.image_id.in_(list(image_ids)))
        )
        return {r.image_id: r.storage_path for r in result}

    @staticmethod
    def to_dict(row: DicomImage) -> dict[str, Any]:
        return {
            "image_id": row.image_id,
            "session_id": row.session_id,
            "patient_id": row.patient_id,
            "storage_path": row.storage_path,
            "sop_instance_uid": row.sop_instance_uid,
            "modality": row.modality,
            "width": row.width,
            "height": row.height,
            "frame_count": row.frame_count,
            "size_bytes": row.size_bytes,
            "original_filename": row.original_filename,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


__all__ = ["DicomImageRepository"]
