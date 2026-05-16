from typing import Any, Dict, List, Optional, Tuple

import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AuditLog
from .verify_utils import verify_chain_integrity
from .critical_audit import CriticalOperationAudit

_logger = structlog.get_logger("app.observability.audit_chain.verifier")


class AuditChainVerifier:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def verify_session_chain(
        self, session_id: str
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        result = await self.db.execute(
            select(AuditLog)
            .where(AuditLog.session_id == session_id)
            .order_by(AuditLog.created_at.asc())
        )
        logs = result.scalars().all()
        return verify_chain_integrity(logs)

    async def verify_all(self) -> Dict[str, Any]:
        session_result = await self.db.execute(
            select(AuditLog)
            .where(AuditLog.session_id.isnot(None))
            .order_by(AuditLog.session_id, AuditLog.created_at.asc())
        )
        all_logs = session_result.scalars().all()

        from itertools import groupby
        session_results = {}
        for session_id, group in groupby(all_logs, key=lambda log: log.session_id):
            logs = list(group)
            valid, broken_log_id, _, _ = verify_chain_integrity(logs)
            session_results[session_id] = (valid, broken_log_id)

        critical_audit = CriticalOperationAudit(self.db)
        critical_results = await critical_audit.verify_all_critical_chains()

        broken_sessions = {k: v for k, v in session_results.items() if not v[0]}
        broken_critical = {k: v for k, v in critical_results.items() if not v[0]}

        return {
            "session_chains": {
                "total": len(session_results),
                "valid": len(session_results) - len(broken_sessions),
                "broken": broken_sessions,
            },
            "critical_chains": {
                "total": len(critical_results),
                "valid": len(critical_results) - len(broken_critical),
                "broken": broken_critical,
            },
            "all_valid": len(broken_sessions) == 0 and len(broken_critical) == 0,
        }
