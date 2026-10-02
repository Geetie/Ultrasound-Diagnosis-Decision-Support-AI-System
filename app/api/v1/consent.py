from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.errors import BadRequestException
from app.config.database import get_db_session
from app.config.redis import RedisPool  # noqa: F401  (保留导入面)
from app.config.redis_keys import RedisKey  # noqa: F401
from app.models.schemas import (
    ConsentAction,
    ConsentQueryResponse,
    ConsentStatus,
    ConsentUpdateRequest,
    ConsentUpdateResponse,
)
from app.repositories.consent_repository import ConsentRepository, record_hsm_degradation_if_needed
from app.security.auth import RoleChecker, get_current_active_user

router = APIRouter(tags=["consent"])
logger = structlog.get_logger()


@router.get("/{patient_id}", response_model=ConsentQueryResponse)
async def get_consent(
    patient_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict = Depends(get_current_active_user),
    _: dict[str, Any] = Depends(RoleChecker(["SYSTEM_ADMIN", "QUALITY_CONTROL", "DEPARTMENT_HEAD"])),
):
    repo = ConsentRepository(db)
    consent_record = await repo.get_latest_consent(patient_id)

    if consent_record:
        current_status = ConsentStatus(consent_record.status)
        return ConsentQueryResponse(
            patient_id=consent_record.patient_id,
            consent_status=current_status,
            consented_at=consent_record.granted_at,
            withdrawn_at=consent_record.withdrawn_at,
            consent_version=consent_record.consent_version,
        )

    return ConsentQueryResponse(
        patient_id=patient_id,
        consent_status=ConsentStatus.DENIED,
        consented_at=None,
        withdrawn_at=None,
        consent_version=None,
    )


@router.post("", response_model=ConsentUpdateResponse)
async def update_consent(
    request: ConsentUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict = Depends(get_current_active_user),
    _: dict[str, Any] = Depends(RoleChecker(["SYSTEM_ADMIN", "QUALITY_CONTROL", "DEPARTMENT_HEAD"])),
):
    """更新知情同意状态"""
    if request.action not in [ConsentAction.GRANT, ConsentAction.WITHDRAW]:
        raise BadRequestException(error_message="无效的操作类型")

    repo = ConsentRepository(db)
    operator_id = current_user.get("user_id") or current_user.get("username")
    if not operator_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cannot identify operator for consent operation",
        )

    consent_status = (
        ConsentStatus.GRANTED
        if request.action == ConsentAction.GRANT
        else ConsentStatus.WITHDRAWN
    )

    consent_record = await repo.create_consent(
        patient_id=request.patient_id,
        status=consent_status,
        consent_version=request.consent_version,
        operator_id=operator_id,
    )

    await record_hsm_degradation_if_needed(db)

    # 撤回事件的 Redis Streams 投递由 ConsentRepository.create_consent 内的
    # _publish_consent_withdrawal 统一完成（含 withdrawn_at 全字段契约）。
    # 此处不再重复投递——历史上这里发过一条缺 withdrawn_at 的第二份事件，
    # 消费端 datetime.fromisoformat 必抛、系统性污染死信流（审计 P0-3）。

    return ConsentUpdateResponse(
        patient_id=consent_record.patient_id,
        consent_status=ConsentStatus(consent_record.status),
        updated_at=datetime.now(timezone.utc),
    )
