"""T-GB-API-02 — 报告生成与签发接口。

链路：``POST /report``（草稿）→ ``POST /report/{id}/sign``（HSM 签名）
→ ``POST /report/{id}/push``（PACS/HIS 推送）。

所有接口均做同意校验（与诊断结果/ FHIR 读取口径一致），签发与推送写入
审计日志（action = report_draft / report_sign / report_push）。
"""
from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, Depends

from app.api.v1.errors import BadRequestException, NotFoundException
from app.api.v1.utils import check_consent_status, get_actor_id, try_renew_session
from app.dependencies import (
    get_audit_service,
    get_consent_repo,
    get_diagnosis_repo,
    get_report_repo,
    get_session_repo,
)
from app.integrations.pacs import get_pacs_adapter
from app.models.schemas import (
    ReportDraftRequest,
    ReportPushRequest,
    ReportResponse,
    ReportSignRequest,
)
from app.security.auth import get_current_active_user
from app.services import report_service
from app.services.audit_service import AuditService

logger = structlog.get_logger("app.api.v1.report")

router = APIRouter(tags=["report"])


def _to_response(report, *, verify: bool = False) -> ReportResponse:
    """报告 ORM 行 → 响应模型。"""
    verified = report_service.verify_report_signature(report) if verify else False
    return ReportResponse(
        report_id=report.report_id,
        request_id=report.request_id,
        session_id=report.session_id,
        patient_id=report.patient_id,
        organ=report.organ,
        status=report.status,
        template_version=report.template_version,
        content_digest=report.content_digest,
        signature_present=bool(report.signature),
        signature_verified=verified,
        signing_key_name=report.signing_key_name,
        signed_at=report.signed_at,
        signed_by=report.signed_by,
        pacs_adapter=report.pacs_adapter,
        pacs_status=report.pacs_status,
        pacs_message=report.pacs_message,
        pacs_attempts=report.pacs_attempts,
        pushed_at=report.pushed_at,
        created_at=report.created_at,
        content=report.content if isinstance(report.content, dict) else None,
        rendered=report.rendered,
    )


async def _require_consent(session_repo, consent_repo, session) -> None:
    """按会话患者做同意校验（撤回/未签署直接拒绝）。"""
    patient_id = getattr(session, "patient_id", None)
    if not patient_id:
        return
    consent_record = await consent_repo.get_latest_consent(patient_id)
    check_consent_status(consent_record, patient_id)


@router.post("", response_model=ReportResponse)
async def create_report_draft(
    payload: ReportDraftRequest,
    report_repo=Depends(get_report_repo),
    diagnosis_repo=Depends(get_diagnosis_repo),
    session_repo=Depends(get_session_repo),
    consent_repo=Depends(get_consent_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    """生成报告草稿（模板填充）；``sign``/``push`` 为 True 时连续执行。"""
    result = await diagnosis_repo.get_result(payload.request_id)
    if not result:
        raise NotFoundException(
            error_message=f"Diagnosis result not found: {payload.request_id}"
        )

    session = await session_repo.get_session(result.session_id) if result.session_id else None
    await _require_consent(session_repo, consent_repo, session)

    actor_id = payload.operator_id or get_actor_id(current_user)

    report = await report_service.generate_report_draft(
        result=result, report_repo=report_repo, session=session
    )

    await audit_service.log_action(
        action="report_draft",
        actor=actor_id,
        session_id=report.session_id,
        target_type="report",
        target_id=report.report_id,
        detail={
            "request_id": report.request_id,
            "organ": report.organ,
            "template_version": report.template_version,
            "content_digest": report.content_digest,
        },
    )

    if payload.sign:
        report = await report_service.sign_report(
            report=report, report_repo=report_repo, actor_id=actor_id
        )
        await audit_service.log_action(
            action="report_sign",
            actor=actor_id,
            session_id=report.session_id,
            target_type="report",
            target_id=report.report_id,
            detail={
                "signing_key_name": report.signing_key_name,
                "content_digest": report.content_digest,
                "source": "draft_request",
            },
        )

    if payload.push:
        if not report.signature:
            raise BadRequestException(
                error_message="报告尚未签名，无法推送（请先 sign=True 或调用 /sign）"
            )
        push_result = await report_service.push_report(
            report=report, report_repo=report_repo
        )
        await audit_service.log_action(
            action="report_push",
            actor=actor_id,
            session_id=report.session_id,
            target_type="report",
            target_id=report.report_id,
            detail=push_result.as_dict(),
        )
        report = await report_repo.get_report(report.report_id) or report

    if session is not None and getattr(session, "session_id", None):
        await try_renew_session(session_repo, session.session_id)

    return _to_response(report, verify=bool(report.signature))


@router.get("/by-request/{request_id}", response_model=ReportResponse)
async def get_report_by_request(
    request_id: str,
    report_repo=Depends(get_report_repo),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    """取某次诊断请求的最新报告。"""
    report = await report_repo.get_latest_by_request(request_id)
    if not report:
        raise NotFoundException(error_message=f"Report not found for request: {request_id}")
    return _to_response(report, verify=bool(report.signature))


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    verify: bool = False,
    report_repo=Depends(get_report_repo),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    """读取报告；``verify=true`` 时附带数字签名校验结果。"""
    report = await report_repo.get_report(report_id)
    if not report:
        raise NotFoundException(error_message=f"Report not found: {report_id}")
    return _to_response(report, verify=verify)


@router.post("/{report_id}/sign", response_model=ReportResponse)
async def sign_report(
    report_id: str,
    payload: ReportSignRequest,
    report_repo=Depends(get_report_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    """对报告正文做 HSM 数字签名。"""
    report = await report_repo.get_report(report_id)
    if not report:
        raise NotFoundException(error_message=f"Report not found: {report_id}")
    if report.signature:
        # 已签名的报告不重复签名（重复签名会让既有校验失效）
        raise BadRequestException(error_message=f"Report already signed: {report_id}")

    actor_id = payload.operator_id or get_actor_id(current_user)
    report = await report_service.sign_report(
        report=report, report_repo=report_repo, actor_id=actor_id
    )

    await audit_service.log_action(
        action="report_sign",
        actor=actor_id,
        session_id=report.session_id,
        target_type="report",
        target_id=report.report_id,
        detail={
            "signing_key_name": report.signing_key_name,
            "content_digest": report.content_digest,
            "source": "sign_endpoint",
        },
    )
    return _to_response(report, verify=True)


@router.post("/{report_id}/push", response_model=ReportResponse)
async def push_report(
    report_id: str,
    payload: ReportPushRequest,
    report_repo=Depends(get_report_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    """把已签名报告推送到 PACS/HIS（推送失败不改变签名状态，可重推）。"""
    report = await report_repo.get_report(report_id)
    if not report:
        raise NotFoundException(error_message=f"Report not found: {report_id}")
    if not report.signature:
        raise BadRequestException(
            error_message="报告尚未签名，无法推送（请先调用 /sign）"
        )

    adapter = get_pacs_adapter()
    if payload.adapter and payload.adapter != getattr(adapter, "name", None):
        from app.integrations.pacs import create_pacs_adapter

        settings_override = type("_S", (), {"pacs_adapter": payload.adapter})()
        adapter = create_pacs_adapter(settings_override)

    actor_id = get_actor_id(current_user)
    push_result = await report_service.push_report(
        report=report, report_repo=report_repo, adapter=adapter
    )

    await audit_service.log_action(
        action="report_push",
        actor=actor_id,
        session_id=report.session_id,
        target_type="report",
        target_id=report.report_id,
        detail=push_result.as_dict(),
    )

    updated = await report_repo.get_report(report_id) or report
    return _to_response(updated, verify=True)


@router.post("/{report_id}/verify")
async def verify_signature(
    report_id: str,
    report_repo=Depends(get_report_repo),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    """校验报告签名与正文是否匹配（正文被改动即失败）。"""
    report = await report_repo.get_report(report_id)
    if not report:
        raise NotFoundException(error_message=f"Report not found: {report_id}")
    return {
        "report_id": report.report_id,
        "signature_present": bool(report.signature),
        "signature_verified": report_service.verify_report_signature(report),
        "content_digest": report.content_digest,
        "signing_key_name": report.signing_key_name,
    }
