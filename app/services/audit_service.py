"""审计日志服务 - 统一的审计日志记录系统"""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AuditLog

logger = structlog.get_logger()

_chain_locks: dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()
_MAX_CHAIN_LOCKS = 10000
_chain_lock_order: list = []


async def _get_chain_lock(session_id: str | None) -> asyncio.Lock:
    key = session_id or "__global__"
    async with _locks_lock:
        if key not in _chain_locks:
            if len(_chain_locks) >= _MAX_CHAIN_LOCKS:
                evict_key = _chain_lock_order.pop(0)
                del _chain_locks[evict_key]
            _chain_locks[key] = asyncio.Lock()
            _chain_lock_order.append(key)
        elif key in _chain_lock_order:
            _chain_lock_order.remove(key)
            _chain_lock_order.append(key)
        return _chain_locks[key]


async def release_chain_lock(session_id: str | None) -> None:
    key = session_id or "__global__"
    async with _locks_lock:
        if key in _chain_locks:
            _chain_locks.pop(key, None)
            if key in _chain_lock_order:
                _chain_lock_order.remove(key)


class AuditService:

    def __init__(self, db: AsyncSession = None):
        self.db = db

    async def _get_last_hash(self, session_id: str | None = None) -> str | None:
        query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(1)
        if session_id:
            query = query.where(AuditLog.session_id == session_id)

        result = await self.db.execute(query)
        last_log = result.scalar_one_or_none()

        if last_log:
            return last_log.hash_self

        return None

    async def _ensure_db(self) -> AsyncSession:
        if self.db is not None:
            return self.db
        from app.config.database import get_session
        async for db_session in get_session():
            self.db = db_session
            return self.db
        raise RuntimeError("Failed to obtain database session for AuditService")

    async def log_action(
        self,
        action: str,
        actor: str,
        session_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        detail: dict[str, Any] | None = None,
        log_type: str = "operation",
    ) -> AuditLog:
        db = await self._ensure_db()
        lock = await _get_chain_lock(session_id)
        async with lock:
            now = datetime.now(timezone.utc)
            log_id = str(uuid.uuid4())

            hash_prev = await self._get_last_hash(session_id)

            hash_content = f"{log_id}-{action}-{actor}-{now.isoformat()}"
            if target_type:
                hash_content = f"{hash_content}-{target_type}"
            if target_id:
                hash_content = f"{hash_content}-{target_id}"
            if detail:
                hash_content = f"{hash_content}-{json.dumps(detail, sort_keys=True, ensure_ascii=False)}"
            if hash_prev:
                hash_content = f"{hash_prev}-{hash_content}"
            hash_self = hashlib.sha256(hash_content.encode()).hexdigest()

            audit_log = AuditLog(
                log_id=log_id,
                session_id=session_id,
                action=action,
                actor=actor,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
                log_type=log_type,
                hash_prev=hash_prev,
                hash_self=hash_self,
                created_at=now,
            )

            db.add(audit_log)

            logger.info(
                "audit_log_recorded",
                action=action,
                actor=actor,
                session_id=session_id,
                target_type=target_type,
                target_id=target_id,
            )

            return audit_log

    async def verify_chain(self, session_id: str | None = None) -> tuple[bool, int | None, str | None, str | None]:
        db = await self._ensure_db()
        query = select(AuditLog).order_by(AuditLog.created_at.asc())
        if session_id:
            query = query.where(AuditLog.session_id == session_id)

        result = await db.execute(query)
        prev_hash = None
        first = True
        for log in result.scalars().all():
            if first:
                first = False
                if log.hash_prev is not None:
                    return False, log.log_id, None, log.hash_prev
                prev_hash = log.hash_self
                continue

            if log.hash_prev != prev_hash:
                return False, log.log_id, prev_hash, log.hash_prev
            prev_hash = log.hash_self

        return True, None, None, None

    async def verify_all_chains(self) -> dict[str, tuple[bool, int | None, str | None, str | None]]:
        db = await self._ensure_db()
        result = await db.execute(
            select(AuditLog)
            .order_by(AuditLog.session_id, AuditLog.created_at.asc())
        )
        all_logs = result.scalars().all()

        from itertools import groupby
        verification_results = {}
        for session_id, group in groupby(all_logs, key=lambda log: log.session_id):
            logs = list(group)
            prev_hash = None
            first = True
            chain_valid = True
            broken_log_id = None
            expected_hash = None
            actual_hash = None

            for log in logs:
                if first:
                    first = False
                    if log.hash_prev is not None:
                        chain_valid = False
                        broken_log_id = log.log_id
                        actual_hash = log.hash_prev
                        break
                    prev_hash = log.hash_self
                    continue

                if log.hash_prev != prev_hash:
                    chain_valid = False
                    broken_log_id = log.log_id
                    expected_hash = prev_hash
                    actual_hash = log.hash_prev
                    break
                prev_hash = log.hash_self

            verification_results[session_id] = (chain_valid, broken_log_id, expected_hash, actual_hash)

        return verification_results


_periodic_task: asyncio.Task | None = None
_periodic_running = False


async def start_periodic_verification(interval_hours: int = 1) -> None:
    global _periodic_task, _periodic_running
    if _periodic_running:
        return
    _periodic_running = True

    async def _run_periodic():
        while _periodic_running:
            await asyncio.sleep(interval_hours * 3600)
            try:
                from app.config.database import get_db_session
                async for db_session in get_db_session():
                    service = AuditService(db_session)
                    results = await service.verify_all_chains()
                    broken = {k: v for k, v in results.items() if not v[0]}
                    if broken:
                        logger.critical(
                            "audit_chain_integrity_violation",
                            broken_chains=len(broken),
                            session_ids=list(broken.keys()),
                        )
                    else:
                        logger.info(
                            "audit_chain_integrity_verified",
                            total_chains=len(results),
                        )
                    break
            except Exception as e:
                logger.error("periodic_audit_verification_failed", error=str(e))

    _periodic_task = asyncio.create_task(_run_periodic())
    logger.info("periodic_audit_verification_started", interval_hours=interval_hours)


async def stop_periodic_verification() -> None:
    global _periodic_running, _periodic_task
    _periodic_running = False
    if _periodic_task:
        _periodic_task.cancel()
        try:
            await _periodic_task
        except asyncio.CancelledError:  # silent-ok: 停机时 await 被取消属正常控制流
            pass
        _periodic_task = None
    logger.info("periodic_audit_verification_stopped")
