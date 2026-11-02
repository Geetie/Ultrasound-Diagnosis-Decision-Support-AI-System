"""审计日志服务 - 统一的审计日志记录系统"""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
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
                # A.117 / G46：调用方若在 detail 里标了 is_critical_operation，
                # 必须镜像到**列**——冷归档只认列，不认 detail。
                is_critical_operation=bool(
                    isinstance(detail, dict) and detail.get("is_critical_operation") is True
                ),
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

    async def verify_all_chains(
        self, since: datetime | None = None
    ) -> dict[str, tuple[bool, int | None, str | None, str | None]]:
        """校验会话级审计链完整性。

        A.131 / G50：支持**增量**校验 —— ``since`` 非空时只校验"自该时刻起有新记录"的会话
        （先查受影响的 session_id，再取这些会话的**完整链**：链完整性必须从头算，
        不能只校验新增段）。此前无参调用会把**全表**审计日志读进内存按会话分组，
        数据量涨上去后每小时一次全表扫描，既慢又吃内存。
        """
        db = await self._ensure_db()

        if since is not None:
            # 1) 只挑"有新活动"的会话（distinct，避免把大量行搬回应用层）
            ids_stmt = (
                select(AuditLog.session_id)
                .where(
                    AuditLog.created_at >= since,
                    AuditLog.session_id.isnot(None),
                )
                .distinct()
            )
            session_ids = [sid for sid in (await db.execute(ids_stmt)).scalars().all() if sid]
            if not session_ids:
                return {}
            # 2) 这些会话的**完整**链（O(受影响会话) 而不是 O(全表)）
            result = await db.execute(
                select(AuditLog)
                .where(AuditLog.session_id.in_(session_ids))
                .order_by(AuditLog.session_id, AuditLog.created_at.asc())
            )
        else:
            result = await db.execute(
                select(AuditLog).order_by(AuditLog.session_id, AuditLog.created_at.asc())
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
#: 上一次周期校验的**开始**时刻（A.131 / G50）：下一次据此做增量扫描。
_last_verified_at: datetime | None = None
#: 增量扫描的重叠窗口：容忍写入端时钟漂移，避免边界附近的行被跳过。
_VERIFY_OVERLAP = timedelta(seconds=60)


async def start_periodic_verification(interval_hours: int = 1) -> None:
    global _periodic_task, _periodic_running
    if _periodic_running:
        return
    _periodic_running = True

    async def _run_periodic():
        global _last_verified_at
        while _periodic_running:
            await asyncio.sleep(interval_hours * 3600)
            try:
                from app.config.database import get_db_session

                # A.131 / G50：首次全量，之后只校验"有新活动"的会话；
                # 重叠 60s 是为了容忍写入端时钟漂移（审计链宁可多校验一段，
                # 也不能因为边界差几毫秒漏掉会话——漏校验=安全问题）。
                since = (
                    _last_verified_at - _VERIFY_OVERLAP
                    if _last_verified_at is not None
                    else None
                )
                started = datetime.now(timezone.utc)
                async for db_session in get_db_session():
                    service = AuditService(db_session)
                    results = await service.verify_all_chains(since=since)
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
                            scan_mode="incremental" if since else "full",
                        )
                    break
                _last_verified_at = started
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
