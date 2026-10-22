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

from app.api.v1.utils import get_actor_id
from app.config.database import get_db_session
from app.dependencies import get_audit_service
from app.models.database import (
    ConsentRecord,
    DiagnosisReport,
    DiagnosisResult,
    DiagnosisSession,
    DicomImage,
    Patient,
)
from app.models.schemas import (
    PatientHistoryItem,
    PatientHistoryResponse,
    PatientHistorySegmentation,
    PatientListItem,
    PatientListResponse,
)
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService
from app.services.sg_output_data import extract_findings_text

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


# ---------------------------------------------------------------------------
# 第二梯队③ 历史对比阅片（A.54）：患者历史检查列表
# ---------------------------------------------------------------------------

# 结论摘要截断长度。为什么截断：对比卡片只展示要点，完整正文应在报告页看；
# 不截断会让 20 次检查的列表 payload 轻易上百 KB（报告正文可达数千字）。
CONCLUSION_PREVIEW_CHARS = 500


def _segmentation_summary(findings) -> PatientHistorySegmentation | None:
    """从判读 findings 抽分割摘要。

    `findings["segmentation"]` **总是存在**（含 available=False + reason，A.51 口径），
    这里保留 reason —— 对比视图要能说清"这次没做分割/为什么"，而不是显示成"没有病灶"。
    """
    if not isinstance(findings, dict):
        return None
    seg = findings.get("segmentation")
    if not isinstance(seg, dict) or not seg:
        return None
    return PatientHistorySegmentation(
        available=bool(seg.get("available")),
        area_mm2=seg.get("area_mm2"),
        area_px=seg.get("area_px"),
        frame_index=seg.get("frame_index"),
        total_frames=seg.get("total_frames"),
        selection_policy=seg.get("selection_policy"),
        organ=seg.get("organ"),
        reason=seg.get("reason"),
        reason_text=seg.get("reason_text"),
    )


def _first_text(value) -> str | None:
    """报告 content 的段落可能是 str / list[str] / list[dict]，统一取一段文本。"""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            got = _first_text(item)
            if got:
                return got
        return None
    if isinstance(value, dict):
        for key in ("text", "content", "value", "diagnosis"):
            got = _first_text(value.get(key))
            if got:
                return got
    return None


def _conclusion_of(report_content, findings) -> str | None:
    """结论摘要：**优先报告「诊断意见」**，无报告再从判读 findings 抽。

    为什么报告优先：报告是已签发/医生可编辑的对外结论（可能被补充修订），
    前后对比应基于"当时对外说了什么"；仅当还没有报告时才退回管线原始结论。
    """
    if isinstance(report_content, dict):
        got = _first_text(report_content.get("diagnosis"))
        if got:
            return got[:CONCLUSION_PREVIEW_CHARS]
    if isinstance(findings, dict):
        got = extract_findings_text(findings.get("fusion_result"))
        if got:
            return got[:CONCLUSION_PREVIEW_CHARS]
    return None


def build_history_items(
    sessions,
    results_by_session: dict,
    reports_by_session: dict,
    image_stats: dict,
) -> list[PatientHistoryItem]:
    """把已取出的行装配成历史检查条目（纯函数，便于守卫测试）。

    - 每次检查取**最近一次**判读结果（同一会话多器官/多轮时以时间为准）；
    - 报告同样取最新一份（草稿→签发→推送会留下多份）；
    - 影像计数与代表帧来自 ``dicom_images``（最早一张 = 该次检查的代表帧）。
    """
    items: list[PatientHistoryItem] = []
    for s in sessions:
        result = results_by_session.get(s.session_id)
        report = reports_by_session.get(s.session_id)
        img_count, first_image_id = image_stats.get(s.session_id, (0, None))
        findings = getattr(result, "findings", None) if result is not None else None
        report_content = getattr(report, "content", None) if report is not None else None
        items.append(
            PatientHistoryItem(
                session_id=s.session_id,
                created_at=s.created_at,
                status=s.status,
                # target_organs 是 JSONB，历史脏数据可能是 str/None —— 统一成 list[str]
                target_organs=_as_str_list(s.target_organs),
                organ=getattr(result, "organ", None) if result is not None else None,
                knowledge_base_version=getattr(s, "knowledge_base_version", None),
                request_id=getattr(result, "request_id", None) if result is not None else None,
                confidence=getattr(result, "confidence", None) if result is not None else None,
                conclusion=_conclusion_of(report_content, findings),
                report_id=getattr(report, "report_id", None) if report is not None else None,
                report_status=getattr(report, "status", None) if report is not None else None,
                report_signed=bool(getattr(report, "signed_at", None)) if report is not None else False,
                signed_at=getattr(report, "signed_at", None) if report is not None else None,
                image_count=int(img_count or 0),
                first_image_id=first_image_id,
                segmentation=_segmentation_summary(findings),
            )
        )
    return items


def _as_str_list(value) -> list[str]:
    """JSONB 里的器官列表 → list[str]（容忍历史脏数据：None / str / 混类型）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


@router.get("/patients/{patient_id}/sessions", response_model=PatientHistoryResponse)
async def list_patient_sessions(
    patient_id: str,
    limit: int = Query(20, ge=1, le=100, description="最多返回多少次检查（按时间倒序）"),
    organ: str | None = Query(None, description="只看该器官有判读结果的检查（对比需同器官）"),
    db: AsyncSession = Depends(get_db_session),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """患者历史检查（历史对比阅片数据源）。

    为什么必须新开端点：`GET /api/v1/sessions` 是**工作列表**语义（只含进行中 +
    今日待处理，**不含 completed**），而历史对比看的恰恰都是已完成检查 —— 拿工作
    列表当历史会导致"刚做完的检查立刻消失"。

    `organ` 过滤作用在**条目级**：一次检查里只有部分器官出了结果时，只保留有该器官
    结果的检查（跨器官对比没有临床意义，宁可不显示也不并排乱比）。
    """
    session_rows = (
        await db.execute(
            select(DiagnosisSession)
            .where(DiagnosisSession.patient_id == patient_id)
            .order_by(DiagnosisSession.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    session_ids = [s.session_id for s in session_rows]

    patient_exists = bool(
        (
            await db.execute(
                select(func.count()).select_from(Patient).where(Patient.patient_id == patient_id)
            )
        ).scalar_one()
    )

    if not session_ids:
        return PatientHistoryResponse(
            patient_id=patient_id, patient_exists=patient_exists, total=0
        )

    # 判读结果：同会话可能多轮/多器官 —— 按时间倒序，每会话取第一条（= 最近）
    result_rows = (
        await db.execute(
            select(DiagnosisResult)
            .where(DiagnosisResult.session_id.in_(session_ids))
            .order_by(DiagnosisResult.created_at.desc(), DiagnosisResult.id.desc())
        )
    ).scalars().all()
    results_by_session: dict = {}
    for r in result_rows:
        results_by_session.setdefault(r.session_id, r)

    # 报告：草稿→签发→推送会留多份 —— 同样每会话取最新一份
    report_rows = (
        await db.execute(
            select(DiagnosisReport)
            .where(DiagnosisReport.session_id.in_(session_ids))
            .order_by(DiagnosisReport.created_at.desc(), DiagnosisReport.id.desc())
        )
    ).scalars().all()
    reports_by_session: dict = {}
    for rep in report_rows:
        reports_by_session.setdefault(rep.session_id, rep)

    # 影像计数 + 代表帧（每会话最早一张）
    image_rows = (
        await db.execute(
            select(DicomImage.session_id, DicomImage.image_id)
            .where(DicomImage.session_id.in_(session_ids))
            .order_by(DicomImage.session_id, DicomImage.created_at, DicomImage.id)
        )
    ).all()
    image_stats: dict = {}
    for sid, image_id in image_rows:
        count, first = image_stats.get(sid, (0, None))
        image_stats[sid] = (count + 1, first if first else image_id)
    image_stats = {sid: (cnt, first) for sid, (cnt, first) in image_stats.items()}

    items = build_history_items(session_rows, results_by_session, reports_by_session, image_stats)

    if organ:
        items = [it for it in items if it.organ == organ]

    organ_counts: dict[str, int] = {}
    for it in items:
        for candidate in ([it.organ] if it.organ else []) + [o for o in it.target_organs if o]:
            organ_counts[candidate] = organ_counts.get(candidate, 0) + 1
    organs = [o for o, _ in sorted(organ_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    comparable_organs = [o for o in organs if organ_counts[o] >= 2]

    # 读患者历史属敏感操作：留审计（谁、看了谁、看了几次）
    await audit_service.log_action(
        action="patient_history_read",
        actor=get_actor_id(current_user),
        target_type="patient",
        target_id=patient_id,
        detail={"returned": len(items), "organ": organ, "limit": limit},
    )

    return PatientHistoryResponse(
        patient_id=patient_id,
        patient_exists=patient_exists,
        total=len(items),
        organs=organs,
        comparable_organs=comparable_organs,
        items=items,
    )
