"""T-GB-SEC-02 — 审计日志冷热分层（PG 热存储 + 对象存储冷归档）。

规格：``04-安全合规.md`` / ``05-可观测性与运维.md``——审计日志与长期保存的
会话数据需要"热层秒级可查 + 冷层长期归档"。

分层的两个关键约束（决定实现形态）：

1. **上传成功前绝不删除热数据**：冷层不可用时整批留待下次重试，只把清单
   置为 ``failed`` 并累计 ``pending``——审计数据丢失不可接受。
2. **审计哈希链不能被撕裂**：``app/observability/audit_chain/verify_utils``
   要求每个会话链的**首个**记录 ``hash_prev is None``，且相邻记录
   ``hash_prev == 前一条 hash_self``。因此冷归档按**整条会话链**处理：
   上传除"创世记录"外的全部记录，创世记录留在热层充当锚点；其余记录整体
   移出后，热层仅剩创世记录（校验仍通过），完整链条存在于冷层 JSONL 中。
   关键操作链（``is_critical_operation``）不参与冷归档，保持实时可验。

冷层保留年限由 ``data_retention_audit_years``（30 年）/ 门诊住院年限约束，
到期清理由 :mod:`app.services.data_retention` 负责。
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.integrations.object_storage import (
    ObjectStorageAdapter,
    build_object_key,
    get_object_storage,
)
from app.models.database import AuditLog, ColdArchiveManifest, SessionArchive
from app.services.audit_service import AuditService

logger = structlog.get_logger("app.services.cold_archive")


CATEGORY_AUDIT_LOGS = "audit_logs"
CATEGORY_SESSION_ARCHIVES = "session_archives"

# 各表在冷层 JSONL 中保留的列（顺序稳定，便于人工查阅与恢复）
_AUDIT_COLUMNS: Sequence[str] = (
    "id",
    "log_id",
    "session_id",
    "action",
    "actor",
    "target_type",
    "target_id",
    "detail",
    "log_type",
    "hash_prev",
    "hash_self",
    "is_critical_operation",
    "cross_chain_hash",
    "created_at",
)

_SESSION_ARCHIVE_COLUMNS: Sequence[str] = (
    "id",
    "session_id",
    "patient_id",
    "device_id",
    "status",
    "target_organs",
    "organ_statuses",
    "knowledge_base_version",
    "consent_status_at_creation",
    "archive_timestamp",
    "created_at",
    "archived_at",
    "patient_type",
)


@dataclass
class ColdArchiveResult:
    """一次冷归档运行的结果。"""

    category: str
    archived: int = 0
    manifests: int = 0
    pending: int = 0
    failed: bool = False
    adapter: str = ""
    message: str = ""
    manifest_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "archived": self.archived,
            "manifests": self.manifests,
            "pending": self.pending,
            "failed": self.failed,
            "adapter": self.adapter,
            "message": self.message,
            "manifest_ids": list(self.manifest_ids),
        }


def _serialize_row(row: Any, columns: Sequence[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column in columns:
        value = getattr(row, column, None)
        if isinstance(value, datetime):
            value = value.isoformat()
        payload[column] = value
    return payload


def _to_jsonl(payloads: Sequence[dict[str, Any]]) -> bytes:
    return "\n".join(
        json.dumps(item, ensure_ascii=False, default=str) for item in payloads
    ).encode("utf-8")


class ColdArchiveService:
    """把超期热数据搬入对象存储冷层，并保留可恢复的清单。

    Args:
        db: 异步会话。
        storage: 对象存储适配器；``None`` 时取进程级适配器。
        batch_size: 单批记录数上限。
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        storage: ObjectStorageAdapter | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.db = db
        self._storage = storage
        self._batch_size = int(batch_size or settings.cold_archive_batch_size)
        self._audit_service = AuditService(db)

    @property
    def storage(self) -> ObjectStorageAdapter:
        return self._storage or get_object_storage()

    # ------------------------------------------------------------------
    # 审计日志：按整条会话链归档
    # ------------------------------------------------------------------

    async def archive_audit_logs(
        self,
        *,
        before: datetime | None = None,
        purge: bool = True,
        max_sessions: int | None = None,
    ) -> ColdArchiveResult:
        """把超期会话的审计链移入冷层（每链保留创世记录作锚点）。

        Args:
            before: 热层保留窗口的截止时间；默认
                ``now - audit_hot_retention_days``。
            purge: 上传成功后是否删除热层记录（默认 True）。
            max_sessions: 本次最多处理的会话数。

        Returns:
            :class:`ColdArchiveResult`；冷层不可用时 ``failed=True`` 且
            **不删除任何热数据**。
        """
        threshold = before or (
            datetime.now(timezone.utc)
            - timedelta(days=settings.audit_hot_retention_days)
        )
        limit_sessions = int(
            max_sessions or settings.cold_archive_max_sessions_per_run
        )
        result = ColdArchiveResult(category=CATEGORY_AUDIT_LOGS)
        result.adapter = self.storage.name

        try:
            session_ids = await self._find_archivable_sessions(
                threshold, limit_sessions
            )
            if not session_ids:
                logger.info("cold_archive_no_archivable_audit_sessions")
                return result

            for session_id in session_ids:
                records, rows = await self._collect_session_chain(session_id, threshold)
                if not records:
                    continue
                if result.archived + len(records) > self._batch_size:
                    break
                outcome = await self._archive_batch(
                    category=CATEGORY_AUDIT_LOGS,
                    records=records,
                    rows=rows,
                    purge=purge,
                    model=AuditLog,
                    time_range=(records[0].get("created_at"), records[-1].get("created_at")),
                )
                if outcome is None:
                    result.failed = True
                    result.pending += len(records)
                    result.message = "object storage unavailable; hot data retained"
                    break
                manifest, archived_count = outcome
                result.archived += archived_count
                result.manifests += 1
                result.manifest_ids.append(manifest.manifest_id)
        except Exception as exc:
            result.failed = True
            result.message = f"{type(exc).__name__}: {exc}"
            logger.error(
                "cold_archive_audit_logs_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        if result.archived > 0:
            await self._audit_service.log_action(
                action="audit_logs_cold_archived",
                actor="system",
                detail=result.as_dict(),
            )
        logger.info("cold_archive_audit_logs_completed", **result.as_dict())
        return result

    async def _find_archivable_sessions(
        self, threshold: datetime, limit_sessions: int
    ) -> list[str]:
        """找出"整条链都已超期"的会话（避免链被时间切分）。

        关键操作记录不参与冷归档（其链路需实时可验）。
        """
        stmt = (
            select(AuditLog.session_id)
            .where(
                AuditLog.session_id.isnot(None),
                AuditLog.is_critical_operation.is_(False),
                AuditLog.created_at < threshold,
            )
            .group_by(AuditLog.session_id)
            .having(func.max(AuditLog.created_at) < threshold)
            .limit(limit_sessions)
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [str(row) for row in rows]

    async def _collect_session_chain(
        self, session_id: str, threshold: datetime
    ) -> tuple[list[dict[str, Any]], list[AuditLog]]:
        """取某会话的全部超期记录（跳过创世记录作热层锚点）。"""
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.session_id == session_id,
                AuditLog.created_at < threshold,
            )
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        if not rows:
            return [], []
        # 创世记录（hash_prev 为 None）留作锚点，保证热层校验仍通过
        if rows[0].hash_prev is None:
            rows = rows[1:]
        if not rows:
            return [], []
        records = [_serialize_row(row, _AUDIT_COLUMNS) for row in rows]
        return records, rows

    # ------------------------------------------------------------------
    # 已归档会话：冷归档后再清理
    # ------------------------------------------------------------------

    async def archive_session_archives(
        self,
        *,
        before: datetime | None = None,
        patient_type: str | None = None,
        purge: bool = True,
    ) -> ColdArchiveResult:
        """把到期的会话归档记录移入冷层。

        Args:
            before: 截止时间；默认按 ``patient_type`` 的保留年限推算
                （门诊 15 年 / 住院 30 年）。
            patient_type: ``outpatient`` / ``inpatient``；``None`` 表示不限。
        """
        if before is None:
            years = (
                settings.data_retention_inpatient_years
                if patient_type == "inpatient"
                else settings.data_retention_outpatient_years
            )
            before = datetime.now(timezone.utc) - timedelta(days=int(years * 365.25))

        result = ColdArchiveResult(category=CATEGORY_SESSION_ARCHIVES)
        result.adapter = self.storage.name

        try:
            conditions = [SessionArchive.archived_at < before]
            if patient_type:
                conditions.append(SessionArchive.patient_type == patient_type)
            stmt = (
                select(SessionArchive)
                .where(*conditions)
                .order_by(SessionArchive.archived_at.asc(), SessionArchive.id.asc())
                .limit(self._batch_size)
            )
            rows = list((await self.db.execute(stmt)).scalars().all())
            if not rows:
                logger.info("cold_archive_no_archivable_session_archives")
                return result

            records = [_serialize_row(row, _SESSION_ARCHIVE_COLUMNS) for row in rows]
            outcome = await self._archive_batch(
                category=CATEGORY_SESSION_ARCHIVES,
                records=records,
                rows=rows,
                purge=purge,
                model=SessionArchive,
                time_range=(records[0].get("archived_at"), records[-1].get("archived_at")),
            )
            if outcome is None:
                result.failed = True
                result.pending += len(records)
                result.message = "object storage unavailable; hot data retained"
                return result

            manifest, archived_count = outcome
            result.archived += archived_count
            result.manifests += 1
            result.manifest_ids.append(manifest.manifest_id)
        except Exception as exc:
            result.failed = True
            result.message = f"{type(exc).__name__}: {exc}"
            logger.error(
                "cold_archive_session_archives_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        if result.archived > 0:
            await self._audit_service.log_action(
                action="session_archives_cold_archived",
                actor="system",
                detail=result.as_dict(),
            )
        logger.info("cold_archive_session_archives_completed", **result.as_dict())
        return result

    # ------------------------------------------------------------------
    # 内部：单批归档
    # ------------------------------------------------------------------

    async def _archive_batch(
        self,
        *,
        category: str,
        records: list[dict[str, Any]],
        rows: list[Any],
        purge: bool,
        model: Any,
        time_range: tuple[str | None, str | None],
    ) -> tuple[ColdArchiveManifest, int] | None:
        """上传一批记录并（可选）清理热数据。

        Returns:
            ``(manifest, 归档记录数)``；对象存储不可用时返回 ``None``
            （调用方据此保留热数据并标记 pending）。
        """
        manifest_id = f"cam_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        object_key = build_object_key(
            settings.object_storage_prefix, category, manifest_id
        )
        payload = _to_jsonl(records)

        manifest = ColdArchiveManifest(
            manifest_id=manifest_id,
            category=category,
            object_key=object_key,
            record_count=len(records),
            first_record_id=getattr(rows[0], "id", None),
            last_record_id=getattr(rows[-1], "id", None),
            size_bytes=len(payload),
            time_range_start=_parse_iso(time_range[0]),
            time_range_end=_parse_iso(time_range[1]),
            status="pending",
            adapter=self.storage.name,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(manifest)
        await self.db.flush()

        started = time.monotonic()
        try:
            stored = await self.storage.put(object_key, payload)
        except Exception as exc:
            manifest.status = "failed"
            manifest.message = f"{type(exc).__name__}: {exc}"[:2000]
            await self.db.flush()
            logger.error(
                "cold_archive_upload_failed",
                manifest_id=manifest_id,
                category=category,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        manifest.status = "archived"
        manifest.checksum = stored.checksum
        manifest.size_bytes = stored.size_bytes
        manifest.archived_at = datetime.now(timezone.utc)

        if purge:
            ids = [row.id for row in rows if getattr(row, "id", None) is not None]
            if ids:
                await self.db.execute(delete(model).where(model.id.in_(ids)))
            manifest.purged_at = datetime.now(timezone.utc)
        await self.db.flush()

        logger.info(
            "cold_archive_batch_archived",
            manifest_id=manifest_id,
            category=category,
            record_count=len(records),
            purged=purge,
            elapsed_ms=round((time.monotonic() - started) * 1000.0, 2),
        )
        return manifest, len(records)

    # ------------------------------------------------------------------
    # 清单读取 / 恢复 / 校验
    # ------------------------------------------------------------------

    async def get_manifest(self, manifest_id: str) -> ColdArchiveManifest | None:
        stmt = select(ColdArchiveManifest).where(
            ColdArchiveManifest.manifest_id == manifest_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_manifests(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ColdArchiveManifest]:
        conditions = []
        if category:
            conditions.append(ColdArchiveManifest.category == category)
        if status:
            conditions.append(ColdArchiveManifest.status == status)
        stmt = (
            select(ColdArchiveManifest)
            .where(*conditions)
            .order_by(ColdArchiveManifest.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def restore_manifest(self, manifest_id: str) -> list[dict[str, Any]]:
        """从冷层读回一批记录（校验 checksum 后再返回）。"""
        manifest = await self.get_manifest(manifest_id)
        if manifest is None:
            raise ValueError(f"manifest not found: {manifest_id}")
        payload = await self.storage.get(manifest.object_key or "")
        if payload is None:
            raise FileNotFoundError(
                f"cold object missing for manifest {manifest_id}: {manifest.object_key}"
            )
        if manifest.checksum is not None:
            from app.services.report_service import compute_digest

            if compute_digest(payload) != manifest.checksum:
                raise ValueError(
                    f"cold object checksum mismatch for manifest {manifest_id}"
                )
        manifest.status = "restored"
        await self.db.flush()
        text = payload.decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    async def verify_manifest(self, manifest_id: str) -> dict[str, Any]:
        """校验冷层对象的存在性 / 校验和 / 记录数。"""
        manifest = await self.get_manifest(manifest_id)
        if manifest is None:
            return {
                "manifest_id": manifest_id,
                "exists": False,
                "valid": False,
                "reason": "manifest not found",
            }
        payload = await self.storage.get(manifest.object_key or "")
        if payload is None:
            return {
                "manifest_id": manifest_id,
                "exists": False,
                "valid": False,
                "reason": "cold object missing",
            }
        from app.services.report_service import compute_digest

        checksum_ok = manifest.checksum is None or compute_digest(payload) == manifest.checksum
        line_count = len([line for line in payload.decode("utf-8").splitlines() if line.strip()])
        count_ok = line_count == (manifest.record_count or 0)
        return {
            "manifest_id": manifest_id,
            "exists": True,
            "valid": bool(checksum_ok and count_ok),
            "checksum_ok": checksum_ok,
            "record_count_ok": count_ok,
            "expected_records": manifest.record_count,
            "actual_records": line_count,
            "status": manifest.status,
        }

    async def stats(self) -> dict[str, Any]:
        """冷归档总览（运维面板口径）。"""
        stmt = select(
            ColdArchiveManifest.category,
            ColdArchiveManifest.status,
            func.count(),
            func.coalesce(func.sum(ColdArchiveManifest.record_count), 0),
        ).group_by(ColdArchiveManifest.category, ColdArchiveManifest.status)
        rows = (await self.db.execute(stmt)).all()
        by_category: dict[str, dict[str, int]] = {}
        for category, status, count, records in rows:
            entry = by_category.setdefault(str(category), {})
            entry[str(status)] = int(count)
            entry["records"] = entry.get("records", 0) + int(records or 0)
        return {"by_category": by_category}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:  # silent-ok: 取值不可解析 → 回退默认（正常控制流）
        return None


__all__ = [
    "CATEGORY_AUDIT_LOGS",
    "CATEGORY_SESSION_ARCHIVES",
    "ColdArchiveResult",
    "ColdArchiveService",
]
