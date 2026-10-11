"""患者列表/检索端点（A.51）。

背景：走查实证「患者列表端点不存在」——建会话时患者只能手工录入 ID 再
走 FHIR 校验（校验+录入补位）。医生需要按姓名/ID 搜索已有患者。

数据源：`patients` 表（FHIR Patient CRUD 的落库处）+ 最近同意状态
（`consent_records`，同患者多条取最新）+ 最近会话时间（`sessions`）。
只读；任意激活用户可查（与 FHIR GET /fhir/Patient/{id} 的读权限同级，
写权限仍由 FHIR/consent 端点把关）。
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db_session
from app.models.database import ConsentRecord, DiagnosisSession, Patient
from app.models.schemas import PatientListItem, PatientListResponse
from app.security.auth import get_current_active_user

logger = structlog.get_logger()
router = APIRouter(tags=["patients"])


@router.get("/patients", response_model=PatientListResponse)
async def list_patients(
    q: str | None = Query(None, description="按 patient_id / 姓名模糊搜索（不区分大小写）"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """患者检索（分页）。q 为空 = 列出最近创建的患者。"""
    conds = []
    if q and q.strip():
        needle = f"%{q.strip()}%"
        conds.append(or_(Patient.patient_id.ilike(needle), Patient.name.ilike(needle)))

    count_stmt = select(func.count()).select_from(Patient)
    base = select(Patient)
    if conds:
        count_stmt = count_stmt.where(*conds)
        base = base.where(*conds)

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(
            base.order_by(Patient.id.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()
    pids = [r.patient_id for r in rows]
    if not pids:
        return PatientListResponse(items=[], total=total, offset=offset, limit=limit)

    # 最近同意状态：同患者多条取 created_at 最新（与 ConsentRepository 同语义）
    consent_rows = (
        await db.execute(
            select(ConsentRecord.patient_id, ConsentRecord.status, ConsentRecord.created_at)
            .where(ConsentRecord.patient_id.in_(pids))
            .order_by(ConsentRecord.patient_id, ConsentRecord.created_at.desc())
        )
    ).all()
    consent_map: dict[str, str] = {}
    for pid, status, _created in consent_rows:
        consent_map.setdefault(pid, status)  # 首次出现即最新（已按时间 desc）

    session_rows = (
        await db.execute(
            select(DiagnosisSession.patient_id, func.max(DiagnosisSession.created_at))
            .where(DiagnosisSession.patient_id.in_(pids))
            .group_by(DiagnosisSession.patient_id)
        )
    ).all()
    last_session = {pid: ts for pid, ts in session_rows}

    return PatientListResponse(
        items=[
            PatientListItem(
                patient_id=r.patient_id,
                name=r.name,
                gender=r.gender,
                consent_status=consent_map.get(r.patient_id),
                last_session_at=last_session.get(r.patient_id),
            )
            for r in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )
