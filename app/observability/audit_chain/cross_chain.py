import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AuditLog

_logger = structlog.get_logger("app.observability.audit_chain.cross_chain")
_cross_chain_lock = asyncio.Lock()


class AuditCrossChain:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate_cross_chain_hash(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> Optional[str]:
        result = await self.db.execute(
            select(AuditLog.hash_self, AuditLog.session_id)
            .where(
                and_(
                    AuditLog.created_at >= period_start,
                    AuditLog.created_at < period_end,
                )
            )
            .order_by(AuditLog.session_id, AuditLog.created_at.asc())
        )
        all_hashes = result.all()

        if not all_hashes:
            return None

        latest_per_session: Dict[str, str] = {}
        for hash_self, session_id in all_hashes:
            if session_id:
                latest_per_session[session_id] = hash_self

        sorted_hashes = sorted(latest_per_session.values())
        combined = "|".join(sorted_hashes)
        return hashlib.sha256(combined.encode()).hexdigest()

    async def record_cross_chain(
        self,
        period_start: datetime,
        period_end: datetime,
        prev_cross_hash: Optional[str] = None,
    ) -> Optional[str]:
        current_hash = await self.generate_cross_chain_hash(period_start, period_end)
        if current_hash is None:
            return None

        hash_content = f"{period_start.isoformat()}|{period_end.isoformat()}|{current_hash}"
        if prev_cross_hash:
            hash_content = f"{prev_cross_hash}|{hash_content}"
        cross_hash_self = hashlib.sha256(hash_content.encode()).hexdigest()

        from app.models.database import AuditCrossChain as AuditCrossChainModel

        record = AuditCrossChainModel(
            id=str(uuid.uuid4()),
            period_start=period_start,
            period_end=period_end,
            prev_cross_hash=prev_cross_hash,
            cross_hash_self=cross_hash_self,
            session_hashes=json.dumps(
                {k: v for k, v in sorted(
                    (await self._get_latest_session_hashes(period_start, period_end)).items()
                )}
            ),
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(record)
        await self.db.flush()

        _logger.info(
            "cross_chain_recorded",
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            cross_hash_self=cross_hash_self[:16],
        )
        return cross_hash_self

    async def _get_latest_session_hashes(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> Dict[str, str]:
        result = await self.db.execute(
            select(AuditLog.hash_self, AuditLog.session_id)
            .where(
                and_(
                    AuditLog.created_at >= period_start,
                    AuditLog.created_at < period_end,
                    AuditLog.session_id.isnot(None),
                )
            )
            .order_by(AuditLog.session_id, AuditLog.created_at.asc())
        )
        latest: Dict[str, str] = {}
        for hash_self, session_id in result.all():
            latest[session_id] = hash_self
        return latest

    async def verify_cross_chain(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        from app.models.database import AuditCrossChain as AuditCrossChainModel

        result = await self.db.execute(
            select(AuditCrossChainModel)
            .where(
                and_(
                    AuditCrossChainModel.period_start == period_start,
                    AuditCrossChainModel.period_end == period_end,
                )
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            return False, None, "cross_chain_record_not_found"

        current_hash = await self.generate_cross_chain_hash(period_start, period_end)
        if current_hash is None:
            return True, None, None

        hash_content = f"{period_start.isoformat()}|{period_end.isoformat()}|{current_hash}"
        if record.prev_cross_hash:
            hash_content = f"{record.prev_cross_hash}|{hash_content}"
        expected_hash = hashlib.sha256(hash_content.encode()).hexdigest()

        if expected_hash != record.cross_hash_self:
            return False, expected_hash[:16], record.cross_hash_self[:16]

        return True, None, None
