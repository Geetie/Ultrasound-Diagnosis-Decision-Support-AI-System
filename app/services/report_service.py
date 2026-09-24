"""T-GB-API-02 — 报告生成与签发。

链路：**模板填充（草稿）→ HSM 数字签名 → PACS/HIS 推送**。

设计要点：

1. **草稿生成为纯内存操作**：只读诊断结果 + 会话 + 模板，无外部 IO，
   满足"报告草稿 ≤ 2 分钟"的验收（实测远低于该预算，见
   ``settings.report_draft_budget_seconds`` 软超时）。
2. **签名对象是渲染后的正文**：``signature`` 覆盖 ``rendered`` 的 UTF-8
   字节，因此校验签名即校验"实际推送出去的那份文本"；摘要
   (``content_digest``) 同时作为幂等键的一部分。
3. **签名密钥独立槽位**：密钥名前缀 ``report_`` 路由到
   :class:`KeySlot.REPORT_SIGNING`，与 JWT / 应急签名密钥隔离；首次签发
   时按需生成（与 ``app/security/auth.py`` 的 JWT 密钥处理一致）。
4. **推送失败不阻塞签发**：报告状态与推送状态分开记录，PACS 不可用时
   报告仍为 ``signed``，仅 ``pacs_status=failed``，可重推。
5. **可注入适配器**：PACS 走 :mod:`app.integrations.pacs`，本地假实现可在
   无 PACS 环境完整验证链路。
"""
from __future__ import annotations

import base64
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import structlog

from app.config.settings import settings
from app.integrations.pacs import (
    PacsPushAdapter,
    PushResult,
    get_pacs_adapter,
)
from app.models.database import DiagnosisReport
from app.repositories.report_repository import ReportRepository

logger = structlog.get_logger("app.services.report_service")


# 报告模板版本（写入 diagnosis_reports.template_version，供审计追溯）
TEMPLATE_VERSION = "report-template-v1"

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "report"

ORGAN_LABELS: Dict[str, str] = {
    "thyroid": "甲状腺",
    "liver": "肝脏",
    "cardiac": "心脏",
}

# 模板文件缺失时的兜底（内容与 app/templates/report/default.md 一致）
_FALLBACK_TEMPLATE = """# 超声诊断报告

- 报告编号：{{report_id}}
- 患者标识：{{patient_id}}
- 检查器官：{{organ_label}}
- 诊断请求：{{request_id}}
- 生成时间：{{generated_at}}
- 知识库版本：{{knowledge_base_version}}
- 模型版本：{{model_version}}

## 一、检查所见

{{findings}}

## 二、诊断意见

{{diagnosis}}

## 三、建议

{{recommendation}}

## 四、证据来源

{{evidence}}

## 五、质量控制

- 融合一致性：{{consistency_status}}
- 人工复核：{{review_required}}
- 报告模板：{{template_version}}

---
本报告由超声诊疗决策支持系统自动生成，须经执业医师审核后方可作为临床诊断依据。
"""


# ----------------------------------------------------------------------
# 模板
# ----------------------------------------------------------------------


def load_template(name: Optional[str] = None) -> str:
    """加载报告模板；文件缺失/不可读时回退内置模板。"""
    template_name = name or settings.report_template_name
    path = TEMPLATE_DIR / template_name
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(
            "report_template_load_failed",
            template=template_name,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _FALLBACK_TEMPLATE


def render_template(template: str, mapping: Mapping[str, Any]) -> str:
    """极简占位符渲染：``{{key}}`` → 值。

    未提供的占位符替换为空串（避免把 ``{{xxx}}`` 原样签发出去）；
    不做表达式求值，避免模板成为注入面。
    """
    rendered = template
    for key, value in mapping.items():
        rendered = rendered.replace("{{" + str(key) + "}}", "" if value is None else str(value))
    # 清理残留占位符
    while "{{" in rendered and "}}" in rendered:
        start = rendered.index("{{")
        end = rendered.index("}}", start)
        rendered = rendered[:start] + rendered[end + 2 :]
    return rendered


# ----------------------------------------------------------------------
# 内容构建
# ----------------------------------------------------------------------


def build_report_content(result: Any, session: Any = None) -> Dict[str, Any]:
    """把诊断结果 + 会话组装为结构化报告内容。

    Args:
        result: ``DiagnosisResult`` ORM 行（含 findings / recommendations /
            confidence / status / clinical_text）。
        session: 可选 ``DiagnosisSession`` 行（提供 patient_id 与 KB 版本）。
    """
    findings_raw = result.findings if isinstance(result.findings, dict) else {}
    fusion = findings_raw.get("fusion_result") or {}
    organ = str(getattr(result, "organ", "") or "")
    knowledge_ids = list(getattr(result, "knowledge_ids", None) or [])
    needs_review = bool(fusion.get("needs_review"))
    confidence = float(getattr(result, "confidence", 0.0) or 0.0)

    clinical_text = str(getattr(result, "clinical_text", "") or "").strip()
    conclusion = str(getattr(result, "recommendations", "") or "").strip()

    return {
        "request_id": str(getattr(result, "request_id", "") or ""),
        "session_id": str(getattr(result, "session_id", "") or ""),
        "patient_id": str(getattr(session, "patient_id", "") or "") or "unknown",
        "organ": organ,
        "organ_label": ORGAN_LABELS.get(organ, organ or "未知"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "knowledge_base_version": str(
            getattr(session, "knowledge_base_version", "")
            or settings.phase0a_mock_kb_version
        ),
        "model_version": settings.phase0a_mock_model_version,
        "diagnosis_status": str(getattr(result, "status", "") or ""),
        "confidence": round(confidence, 4),
        # 检查所见：医生提交的临床文本（含语音转写）即最可靠的一手描述
        "findings": clinical_text or "（未提供临床描述）",
        # 诊断意见：融合层输出
        "diagnosis": conclusion or "（模型未产出结论，请人工评估）",
        "recommendation": (
            "建议执业医师复核后再行签发。" if needs_review else "建议结合临床与随访观察。"
        ),
        "evidence": (
            "、".join(knowledge_ids) if knowledge_ids else "（本次结论无引用知识条目）"
        ),
        "evidence_chunk_count": len(knowledge_ids),
        "consistency_status": "需人工复核" if needs_review else "一致",
        "review_required": "是" if needs_review else "否",
        "review_reason": fusion.get("review_reason"),
        "timings_ms": findings_raw.get("timings_ms") or {},
        "template_version": TEMPLATE_VERSION,
    }


def render_report(content: Mapping[str, Any]) -> str:
    """按模板渲染报告正文。"""
    return render_template(load_template(), content)


def report_payload(rendered: str) -> bytes:
    """签名 / 推送 / 摘要所依据的规范载荷。"""
    return rendered.encode("utf-8")


def compute_digest(payload: bytes) -> str:
    """载荷 SHA-256（十六进制）。"""
    return hashlib.sha256(payload).hexdigest()


# ----------------------------------------------------------------------
# 签名
# ----------------------------------------------------------------------


def _ensure_signing_key() -> str:
    """取得（必要时生成）报告签名密钥，返回密钥名。"""
    from app.security.hsm import KeySlot, hsm_manager

    key_name = settings.report_signing_key_name
    if hsm_manager.get_key_info(key_name) is None:
        logger.info("report_signing_key_not_found_creating", key_name=key_name)
        hsm_manager.generate_key_pair(
            key_name,
            key_size=settings.report_signing_key_size,
            slot=KeySlot.REPORT_SIGNING,
        )
    return key_name


def sign_payload(payload: bytes) -> Dict[str, str]:
    """对载荷做数字签名。

    Returns:
        ``{"signature": base64, "key_name": str}``

    Raises:
        RuntimeError: HSM 不可用或处于禁止签名的降级阶段（由调用方决定
            是否降级为"未签名草稿"）。
    """
    from app.security.hsm import hsm_manager

    key_name = _ensure_signing_key()
    signature = hsm_manager.sign(key_name, payload)
    return {
        "signature": base64.b64encode(signature).decode("ascii"),
        "key_name": key_name,
    }


def verify_report_signature(report: DiagnosisReport) -> bool:
    """校验报告签名是否与正文匹配（正文被改动即失败）。"""
    if not report.signature or not report.rendered:
        return False
    from app.security.hsm import hsm_manager

    try:
        signature = base64.b64decode(report.signature)
    except Exception:
        return False
    key_name = report.signing_key_name or settings.report_signing_key_name
    try:
        return bool(
            hsm_manager.verify_signature(
                key_name, report_payload(report.rendered), signature
            )
        )
    except Exception as exc:
        logger.warning(
            "report_signature_verify_failed",
            report_id=getattr(report, "report_id", None),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


# ----------------------------------------------------------------------
# 编排：草稿 / 签发 / 推送
# ----------------------------------------------------------------------


async def generate_report_draft(
    *,
    result: Any,
    report_repo: ReportRepository,
    session: Any = None,
) -> DiagnosisReport:
    """生成（或复用）报告草稿。

    同一诊断请求若已有草稿/已签名报告，直接复用（避免重复编号与
    重复签名）；需要按新结果重出报告时由调用方显式新建。

    Returns:
        落库后的 ``DiagnosisReport``。
    """
    started = time.monotonic()

    existing = await report_repo.get_latest_by_request(
        str(getattr(result, "request_id", "") or "")
    )
    if existing is not None:
        logger.info(
            "report_draft_reused",
            report_id=existing.report_id,
            request_id=existing.request_id,
            status=existing.status,
        )
        return existing

    content = build_report_content(result, session)
    rendered = render_report(content)
    payload = report_payload(rendered)

    report = await report_repo.create_report(
        request_id=content["request_id"],
        session_id=content["session_id"] or None,
        patient_id=content["patient_id"],
        organ=content["organ"] or None,
        content=content,
        rendered=rendered,
        content_digest=compute_digest(payload),
        template_version=TEMPLATE_VERSION,
    )

    elapsed = time.monotonic() - started
    if elapsed > settings.report_draft_budget_seconds:
        logger.warning(
            "report_draft_over_budget",
            report_id=report.report_id,
            elapsed_seconds=round(elapsed, 2),
            budget_seconds=settings.report_draft_budget_seconds,
        )
    else:
        logger.info(
            "report_draft_generated",
            report_id=report.report_id,
            elapsed_ms=round(elapsed * 1000.0, 2),
        )
    return report


async def sign_report(
    *,
    report: DiagnosisReport,
    report_repo: ReportRepository,
    actor_id: Optional[str] = None,
) -> DiagnosisReport:
    """对报告正文签名并置为 ``signed``。"""
    payload = report_payload(report.rendered or "")
    signed = sign_payload(payload)
    updated = await report_repo.mark_signed(
        report.report_id,
        signature=signed["signature"],
        signing_key_name=signed["key_name"],
        signed_by=actor_id,
    )
    return updated or report


async def push_report(
    *,
    report: DiagnosisReport,
    report_repo: ReportRepository,
    adapter: Optional[PacsPushAdapter] = None,
) -> PushResult:
    """把已签名报告推送到 PACS/HIS。

    - 未签名的报告**拒绝推送**（返回 skipped 结果，不调用适配器）；
    - 推送失败只记录状态，不改变 ``signed``。
    """
    if not report.signature:
        logger.warning(
            "report_push_skipped_unsigned", report_id=report.report_id
        )
        await report_repo.mark_pacs_result(
            report.report_id,
            adapter=(adapter or get_pacs_adapter()).name,
            success=False,
            message="report is not signed",
            attempts=0,
            skipped=True,
        )
        return PushResult(
            success=False,
            adapter=(adapter or get_pacs_adapter()).name,
            message="report is not signed",
            attempts=0,
            skipped=True,
        )

    pacs = adapter or get_pacs_adapter()
    payload = report_payload(report.rendered or "")
    try:
        result = await pacs.push_report(
            report_id=report.report_id,
            payload=payload,
            idempotency_key=f"{report.report_id}:{report.content_digest or ''}",
            patient_id=report.patient_id,
            organ=report.organ,
        )
    except Exception as exc:
        logger.error(
            "report_push_adapter_raised",
            report_id=report.report_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        result = PushResult(
            success=False,
            adapter=getattr(pacs, "name", "unknown"),
            message=f"{type(exc).__name__}: {exc}",
            attempts=1,
        )

    await report_repo.mark_pacs_result(
        report.report_id,
        adapter=result.adapter,
        success=result.success,
        message=result.message,
        attempts=result.attempts,
        skipped=result.skipped,
    )
    return result


def list_evidence_ids(report: DiagnosisReport) -> List[str]:
    """从报告内容中提取证据条目（供 FHIR/接口透出）。"""
    content = report.content if isinstance(report.content, dict) else {}
    raw = str(content.get("evidence") or "")
    if not raw or raw.startswith("（"):
        return []
    return [item for item in raw.split("、") if item]


__all__ = [
    "ORGAN_LABELS",
    "TEMPLATE_VERSION",
    "build_report_content",
    "compute_digest",
    "generate_report_draft",
    "list_evidence_ids",
    "load_template",
    "push_report",
    "render_report",
    "render_template",
    "report_payload",
    "sign_payload",
    "sign_report",
    "verify_report_signature",
]
