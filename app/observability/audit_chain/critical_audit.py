import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AuditLog
from app.config.settings import settings
from .verify_utils import verify_chain_integrity

_logger = structlog.get_logger("app.observability.audit_chain.critical_audit")
_critical_chain_lock = asyncio.Lock()


class CriticalOperationAudit:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log_critical_operation(
        self,
        operation: str,
        operator: str,
        before_value: Optional[str] = None,
        after_value: Optional[str] = None,
        session_id: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        try:
            async with _critical_chain_lock:
                now = datetime.now(timezone.utc)
                log_id = str(uuid.uuid4())

                prev_hash = await self._get_last_critical_hash(operation)

                hash_content = f"{log_id}-{operation}-{operator}-{now.isoformat()}"
                if before_value:
                    hash_content = f"{hash_content}-before:{before_value}"
                if after_value:
                    hash_content = f"{hash_content}-after:{after_value}"
                if prev_hash:
                    hash_content = f"{prev_hash}-{hash_content}"
                hash_self = hashlib.sha256(hash_content.encode()).hexdigest()

                enriched_detail = detail or {}
                enriched_detail["is_critical_operation"] = True
                enriched_detail["before_value"] = before_value
                enriched_detail["after_value"] = after_value

                audit_log = AuditLog(
                    log_id=log_id,
                    session_id=session_id,
                    action=operation,
                    actor=operator,
                    target_type="critical_operation",
                    target_id=operation,
                    detail=enriched_detail,
                    log_type="critical",
                    hash_prev=prev_hash,
                    hash_self=hash_self,
                    created_at=now,
                )

                self.db.add(audit_log)

                _logger.info(
                    "critical_operation_audited",
                    operation=operation,
                    operator=operator,
                    session_id=session_id,
                )

                return audit_log
        except Exception as e:
            _logger.error("log_critical_operation_failed", operation=operation, error=str(e), exc_info=True)
            raise

    async def _get_last_critical_hash(self, operation: str) -> Optional[str]:
        result = await self.db.execute(
            select(AuditLog)
            .where(
                and_(
                    AuditLog.log_type == "critical",
                    AuditLog.action == operation,
                )
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
        last_log = result.scalar_one_or_none()
        return last_log.hash_self if last_log else None

    async def verify_critical_chain(self, operation: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        result = await self.db.execute(
            select(AuditLog)
            .where(
                and_(
                    AuditLog.log_type == "critical",
                    AuditLog.action == operation,
                )
            )
            .order_by(AuditLog.created_at.asc())
        )
        logs = result.scalars().all()
        return verify_chain_integrity(logs)

    async def verify_all_critical_chains(self) -> Dict[str, Tuple[bool, Optional[str], Optional[str], Optional[str]]]:
        result = await self.db.execute(
            select(AuditLog)
            .where(AuditLog.log_type == "critical")
            .order_by(AuditLog.action, AuditLog.created_at.asc())
        )
        all_logs = result.scalars().all()

        from itertools import groupby
        verification_results = {}
        for action, group in groupby(all_logs, key=lambda log: log.action):
            logs = list(group)
            verification_results[action] = verify_chain_integrity(logs)

        return verification_results


def is_critical_operation(action: str) -> bool:
    return action in (settings.obs_audit_critical_operations or [])
