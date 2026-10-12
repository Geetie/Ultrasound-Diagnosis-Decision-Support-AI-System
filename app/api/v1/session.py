from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.v1.errors import NotFoundException
from app.api.v1.utils import check_consent_status, get_actor_id
from app.config.settings import settings
from app.dependencies import get_audit_service, get_consent_repo, get_session_repo
from app.models.schemas import (
    ConsentStatus,
    OrganStatus,
    SessionCreateRequest,
    SessionListItem,
    SessionListResponse,
    SessionResponse,
    SessionStatus,
    SessionUpdateRequest,
)
from app.repositories import ConsentRepository, SessionRepository
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService

router = APIRouter(tags=["sessions"])

SESSION_TTL_SECONDS = 7200


def _calc_ttl_remaining(updated_at: datetime | None) -> int | None:
    if not updated_at:
        return None
    elapsed = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return max(0, int(SESSION_TTL_SECONDS - elapsed))


def _build_session_response(session, demographics: dict | None = None) -> SessionResponse:
    d = demographics or {}
    return SessionResponse(
        session_id=session.session_id,
        patient_id=session.patient_id,
        device_id=session.device_id,
        status=SessionStatus(session.status),
        target_organs=session.target_organs,
        organ_statuses={k: OrganStatus(v) for k, v in session.organ_statuses.items()},
        knowledge_base_version=session.knowledge_base_version,
        created_at=session.created_at,
        updated_at=session.updated_at,
        consent_status_at_creation=ConsentStatus(session.consent_status_at_creation),
        ttl_remaining_seconds=_calc_ttl_remaining(session.updated_at),
        patient_name_masked=_mask_name(d.get("name")),
        gender=d.get("gender"),
        age=_age_from(d.get("birth_date")),
    )


def _mask_name(name: str | None) -> str | None:
    """姓名脱敏：姓 + **。

    列表是共享屏幕，暴露全名即泄露面。**脱敏必须在服务端做** ——
    前端拿到再遮，等于把完整姓名发到了浏览器。
    单名/双名/英文名都要覆盖：
      张三 → 张**   欧阳修→ 欧**   李 →李**
    """
    if not name:
        return None
    first = name[0]
    return f"{first}{'*' * 2}"


def _age_from(birth_date) -> int | None:
    """从出生日期派生年龄。缺失或非法一律 None —— 不猜。"""
    if not birth_date:
        return None
    now = datetime.now(birth_date.tzinfo or timezone.utc)
    years = now.year - birth_date.year
    # 生日未到则减一
    if (now.month, now.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years if 0 <= years < 130 else None


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 20,
    session_repo: SessionRepository = Depends(get_session_repo),
    current_user: dict | None = Depends(get_current_active_user),
):
    """工作列表 —— 设计稿 01_工作列表 / 15_工作列表浅色 的数据源。

    为什么必须有这个端点：原先只有 POST /sessions（建）与
    GET /sessions/{id}（单个），**没有列表** —— 前端工作列表无数据源，
    只能写死三行假数据。

    排序与分组都在服务端完成，前端只渲染。
    契约 §0 P2：只分两组（进行中 / 今日待处理），不按七种状态切页。
    """
    # 进行中 = active / partial；待处理 = needs_review / pending。
    # 不含 completed / failed / terminated —— 那些不是"待处理"。
    rows = await session_repo.list_sessions(limit=limit)

    in_progress: list[SessionListItem] = []
    pending_today: list[SessionListItem] = []
    today = datetime.now(timezone.utc).date()

    for r in rows:
        item = SessionListItem(
            session_id=r["session_id"],
            patient_id=r["patient_id"],
            patient_name_masked=_mask_name(r.get("name")),
            gender=r.get("gender"),
            age=_age_from(r.get("birth_date")),
            status=SessionStatus(r["status"]),
            target_organs=r.get("target_organs") or [],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            ttl_remaining_seconds=_calc_ttl_remaining(r["updated_at"]),
        )
        if item.status in (SessionStatus.ACTIVE, SessionStatus.PARTIAL):
            in_progress.append(item)
        elif item.status in (SessionStatus.PENDING, SessionStatus.NEEDS_REVIEW):
            # 只显示今天的；昨天的待处理不该挤在今天列表里
            if item.created_at.date() == today:
                pending_today.append(item)

    return SessionListResponse(
        in_progress=in_progress,
        pending_today=pending_today,
        # 只数待处理：画布 01 的「今日 3 例待处理」指下面那组，不含进行中那一行。
        # 把进行中也计入会让医生以为还有 4 份要看。
        total_pending=len(pending_today),
    )


@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    consent_repo: ConsentRepository = Depends(get_consent_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    consent_record = await consent_repo.get_latest_consent(request.patient_id)
    consent_status = check_consent_status(consent_record, request.patient_id)

    session = await session_repo.create_session(
        patient_id=request.patient_id,
        device_id=request.device_id,
        target_organs=request.target_organs,
        consent_status=consent_status,
        # A.52：会话落库的**知识库版本**必须是本次判读真正会检索的那个库
        # （报告模板会渲染它）。此前缺省硬编码 kb_v1.0.0 占位。
        knowledge_base_version=settings.rag_kb_version,
    )

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="session_create",
        actor=actor_id,
        session_id=session.session_id,
        target_type="patient",
        target_id=request.patient_id,
        detail={
            "device_id": request.device_id,
            "target_organs": request.target_organs,
            "consent_status": consent_status.value,
        },
    )

    return _build_session_response(session)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    session_repo: SessionRepository = Depends(get_session_repo),
    current_user: dict | None = Depends(get_current_active_user),
):
    session = await session_repo.get_session(session_id)
    if not session:
        raise NotFoundException(error_message=f"Session not found: {session_id}")

    # A.36 P0：详情响应带脱敏人口学 —— 工作区身份数据驱动，不再写死
    demographics = await session_repo.get_patient_demographics(session.patient_id)
    return _build_session_response(session, demographics)


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    request: SessionUpdateRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    session = await session_repo.update_session(
        session_id=session_id,
        status=request.status,
        organ_statuses=request.organ_statuses,
    )

    if not session:
        raise NotFoundException(error_message=f"Session not found: {session_id}")

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="session_update",
        actor=actor_id,
        session_id=session_id,
        detail={
            "status": request.status.value if request.status else None,
            "organ_statuses": request.organ_statuses,
        },
    )

    return _build_session_response(session)
