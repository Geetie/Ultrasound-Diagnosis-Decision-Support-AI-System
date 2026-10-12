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
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

ORGAN_LABELS: dict[str, str] = {
    "thyroid": "甲状腺",
    "liver": "肝脏",
    "cardiac": "心脏",
}

# M8 报告模板库：器官 → 模板文件名。未登记的器官回退
# settings.report_template_name（默认 default.md）。
ORGAN_TEMPLATES: dict[str, str] = {
    "thyroid": "thyroid_standard.md",
    "liver": "liver_standard.md",
    "cardiac": "cardiac_standard.md",
}

# M8 完整性校验：通用必需字段 + 器官级必需字段。
# 通用字段缺失说明报告"根本没有内容"；器官级字段缺失说明"该器官该有的段落
# 没填"（例如甲状腺报告缺测量数据），两者都要在完整性面板里显示出来。
REQUIRED_FIELDS_COMMON: tuple = ("request_id", "organ", "generated_at", "template_version")
REQUIRED_FIELDS_BY_ORGAN: dict[str, tuple] = {
    "thyroid": ("findings", "diagnosis", "measurement_summary", "clinical_actions"),
    "liver": ("findings", "diagnosis", "measurement_summary", "clinical_actions"),
    "cardiac": ("findings", "diagnosis", "clinical_actions"),
}

# ----------------------------------------------------------------------
# 签发门禁（2026-10-06，A.32）
#
# 报告各段落的临床权重是**不对称**的，签发门禁必须体现这种不对称：
#
#   findings（超声所见）—— 证据段。所见是超声报告的主体：形态、回声、病灶的
#       位置/数目/大小/边界。没有它，诊断意见是**无证据的断言**——而本系统
#       融合层 / Self-RAG / 安全网关防的恰恰就是"无证据的断言"。缺它不允许签。
#   diagnosis（诊断意见）—— 结论段。报告存在的意义就是给出它；缺了它，
#       报告对临床决策零贡献，签发等于签署一份空结论文书。
#
#   measurement_summary（测量摘要）—— 量化证据。TI-RADS 分级依赖长径，
#       病灶阳性时必须有；但正常扫查可以是"器官大小正常范围"一类的定性描述，
#       且部分检查确实无可测量项 → 归入知情确认档，不硬禁。
#   clinical_actions（处置建议）—— 建议段。部分检查确实无额外处置
#       （随访即可），医生知情确认后签发是合理的 → 知情确认档。
#
# 这份清单是**临床口径**：改它等于改签发政策，须经项目组/合规评审，
# 并同步更新 tests/unit/api/test_report_sign_gate.py 与 18 号手册。
# ----------------------------------------------------------------------
SIGN_BLOCKING_SECTIONS: tuple = ("findings", "diagnosis")

# 段落中文名（签发门禁的错误消息用）。前端 FIELD_LABELS 与此同源——
# 两边各自维护，测试互相钉住（test_report_sign_gate 断言中文名出现在错误里）。
SECTION_LABELS: dict[str, str] = {
    "findings": "超声所见",
    "diagnosis": "诊断意见",
    "measurement_summary": "测量摘要",
    "clinical_actions": "处置建议",
    "recommendation": "建议",
    "request_id": "诊断请求 ID",
    "organ": "检查器官",
    "generated_at": "生成时间",
    "template_version": "模板版本",
}


def section_label(field: str) -> str:
    return SECTION_LABELS.get(field, field)

# 允许医生补录的段落（全部是 build_report_content 产出的**已渲染字符串**，
# 医生输入直接替换该段正文；见 A.31 的调研结论）。
SUPPLEMENTABLE_SECTIONS: tuple = (
    "findings",
    "diagnosis",
    "measurement_summary",
    "clinical_actions",
    "recommendation",
)

# 视为"未填写"的占位前缀（build_report_content 用它们标注缺内容的情况）
_PLACEHOLDER_PREFIXES: tuple = ("（未提供", "（模型未产出", "（本次")

# 模板文件缺失时的兜底（内容与 app/templates/report/default.md 一致）
_FALLBACK_TEMPLATE = """# 超声诊断报告

- 报告编号：{{report_id}}
- 患者标识：{{patient_id}}
- 检查器官：{{organ_label}}
- 诊断请求：{{request_id}}
- 生成时间：{{generated_at}}
- 知识库版本：{{knowledge_base_version}}
- 模型版本（诊断引擎）：{{model_version}}
- 生成模型：{{llm_model}}

## 一、检查所见

{{findings}}

## 二、影像分割（模型辅助）

{{nodule_summary}}

## 三、诊断意见

{{diagnosis}}

## 四、建议

{{recommendation}}

## 五、证据来源

{{evidence}}

## 六、质量控制

- 融合一致性：{{consistency_status}}
- 人工复核：{{review_required}}
- 报告模板：{{template_version}}

---
本报告由超声诊疗决策支持系统自动生成，须经执业医师审核后方可作为临床诊断依据。
"""


# ----------------------------------------------------------------------
# 模板
# ----------------------------------------------------------------------

# 并发审查 P1-1：模板文本进程内缓存（模板为启动期固定资产，避免每份报告一次
# 同步磁盘读阻塞事件循环）。模板名空间极小，用 dict + 上界即可。
_TEMPLATE_CACHE: dict[str, str] = {}
_TEMPLATE_CACHE_MAX = 32


def select_template_name(
    organ: str | None = None, name: str | None = None
) -> str:
    """选定模板文件名。

    优先级：显式 ``name`` > 器官模板 > ``settings.report_template_name``。
    显式指定优先，便于运维临时指定模板排查问题。
    """
    if name:
        return name
    organ_key = (organ or "").strip().lower()
    organ_template = ORGAN_TEMPLATES.get(organ_key)
    if organ_template and (TEMPLATE_DIR / organ_template).exists():
        return organ_template
    if organ_template:
        logger.warning(
            "report_organ_template_missing", organ=organ_key, template=organ_template
        )
    return settings.report_template_name


def load_template(name: str | None = None, organ: str | None = None) -> str:
    """加载报告模板；文件缺失/不可读时回退内置模板。

    并发审查 P1-1：原实现每份报告都 ``read_text`` 一次（同步 IO 在事件循环里），
    且 ``select_template_name`` 还要 ``.exists()`` 一次 stat。模板是启动期就固定
    好的静态资产，故按最终模板名做进程内缓存（``reload_templates()`` 可显式失效）。
    """
    template_name = select_template_name(organ=organ, name=name)
    cached = _TEMPLATE_CACHE.get(template_name)
    if cached is not None:
        return cached
    path = TEMPLATE_DIR / template_name
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning(
            "report_template_load_failed",
            template=template_name,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _FALLBACK_TEMPLATE
    if len(_TEMPLATE_CACHE) >= _TEMPLATE_CACHE_MAX:
        _TEMPLATE_CACHE.clear()
    _TEMPLATE_CACHE[template_name] = text
    return text


def reload_templates() -> None:
    """清空模板缓存（模板文件热更新后调用）。"""
    _TEMPLATE_CACHE.clear()


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return True
        return text.startswith(_PLACEHOLDER_PREFIXES)
    if isinstance(value, (list, dict, tuple)):
        return len(value) == 0
    return False


def check_completeness(content: Mapping[str, Any], organ: str | None = None) -> dict[str, Any]:
    """M8 报告完整性校验：必需段落/字段是否齐备。

    返回结构直接供接口与前端完整性面板使用：``passed`` 为 False 时前端高亮
    缺失项，医生可回到诊断页补录（例如补上临床描述或测量数据）。
    """
    organ_key = (organ or str(content.get("organ") or "")).strip().lower()
    required = list(REQUIRED_FIELDS_COMMON) + list(REQUIRED_FIELDS_BY_ORGAN.get(organ_key, ()))
    missing = [field for field in required if _is_missing(content.get(field))]
    return {
        "organ": organ_key,
        "template": select_template_name(organ=organ_key),
        "required": required,
        "missing": missing,
        "blocking": [f for f in missing if f in SIGN_BLOCKING_SECTIONS],
        "passed": not missing,
    }


def missing_sign_blockers(completeness: Mapping[str, Any]) -> list[str]:
    """签发硬门禁：缺失且**不允许知情确认豁免**的段落。

    与 ``completeness["blocking"]`` 同源，这里单独给一个函数是因为
    签发端点要的是"能不能签"，而不是完整校验报告 —— 语义分开，
    测试也分开钉（改 ``SIGN_BLOCKING_SECTIONS`` 时两处一起炸）。
    """
    missing = completeness.get("missing") or []
    return [f for f in missing if f in SIGN_BLOCKING_SECTIONS]


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


_MEASUREMENT_ROW_LABELS: tuple = (
    ("long_mm", "长径", "mm"),
    ("short_mm", "短径", "mm"),
    ("anteroposterior_mm", "前后径", "mm"),
    ("aspect_ratio", "纵横比", ""),
    ("volume_mm3", "体积", "mm³"),
    ("distance_to_capsule_mm", "距包膜", "mm"),
    ("distance_to_organ_edge_mm", "距脏器边缘", "mm"),
    ("distance_to_skin_mm", "距皮肤", "mm"),
)

_PIXEL_SPACING_SOURCE_LABELS: dict[str, str] = {
    "dicom": "DICOM 像素间距",
    "default": "估算（缺 PixelSpacing）",
    "text": "报告文本",
}


def _format_measurement(measurement: Any) -> str:
    """M3 测量摘要文本。"""
    if not isinstance(measurement, Mapping) or not measurement:
        return "（未提供测量数据）"
    parts = [
        f"{label} {measurement[key]}{unit}"
        for key, label, unit in _MEASUREMENT_ROW_LABELS
        if measurement.get(key) is not None
    ]
    if not parts:
        return "（未提供测量数据）"
    source = _PIXEL_SPACING_SOURCE_LABELS.get(str(measurement.get("pixel_spacing_source")))
    if source:
        parts.append(f"来源：{source}")
    return "；".join(parts)


def _format_nodule_segmentation(segmentation: Any) -> str:
    """分割旁路摘要（2026-10-03）。

    语义要求（临床安全）：
      - ``available=True`` → 报面积/占比/框选，**并显式标注这是模型辅助结果**；
      - ``available=False`` → 写明原因，**绝不能**写成"未检出"（两者含义
        完全不同：前者是能力没跑，后者是跑了且没找到目标）。
    """
    if not isinstance(segmentation, Mapping) or not segmentation:
        return "（本次未执行影像分割）"
    if not segmentation.get("available"):
        reason = str(segmentation.get("reason") or "未提供原因")
        return f"（未执行影像分割：{reason}）"

    parts: list[str] = []
    area_px = segmentation.get("area_px")
    area_mm2 = segmentation.get("area_mm2")
    ratio = segmentation.get("lesion_area_ratio")
    if isinstance(area_px, int | float) and area_px > 0:
        parts.append(f"分割面积 {round(float(area_px))} 像素²")
    if isinstance(area_mm2, int | float) and area_mm2 > 0:
        # 有 PixelSpacing 才换算 mm²（旁路在缺间距时返回 None，不猜默认值）
        parts.append(f"约 {round(float(area_mm2), 1)} mm²")
    if isinstance(ratio, int | float):
        parts.append(f"占画面 {round(float(ratio) * 100, 2)}%")
    bbox = segmentation.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        parts.append(
            f"边界框 {bbox[0]},{bbox[1]} → {bbox[2]},{bbox[3]}（原图像素坐标）"
        )
    if not parts:
        return "（分割未检出目标区域）"
    # A.51 多帧分期③：帧归属必须进报告——多帧栈的分割结果来自"择优帧"，
    # 不写明帧号医生无法回看核对（此前只落 JSON 元数据，报告层不可见）。
    total_frames = segmentation.get("total_frames")
    frame_index = segmentation.get("frame_index")
    if isinstance(total_frames, int) and total_frames > 1 and isinstance(frame_index, int):
        policy = segmentation.get("selection_policy")
        tag = "多帧候选择优" if policy == "best_mean_prob" else "中间帧"
        parts.append(f"取自第 {frame_index + 1}/{total_frames} 帧（{tag}）")
    organ = segmentation.get("organ") or "目标"
    return f"模型辅助分割（{organ}）：" + "；".join(parts)


def _format_actions(actions: Any) -> str:
    """M7 处置建议段落文本。"""
    placeholder = "（本次未生成结构化处置建议，请结合临床判断）"
    if not isinstance(actions, (list, tuple)) or not actions:
        return placeholder
    lines = []
    for index, item in enumerate(actions, start=1):
        if not isinstance(item, Mapping):
            continue
        segments = [f"{index}. {item.get('action_type', '')}"]
        if item.get("target_department"):
            segments.append(f"科室：{item['target_department']}")
        if item.get("urgency"):
            segments.append(f"紧急度：{item['urgency']}")
        level = item.get("applicable_level")
        if level and level != "通用":
            segments.append(f"适用：{level}")
        if item.get("description"):
            segments.append(str(item["description"]))
        lines.append(" | ".join(segments))
    return "\n".join(lines) if lines else placeholder


def _format_recommendation(needs_review: bool, actions: Any) -> str:
    """建议段：有结构化处置建议时以它为准，否则保持原有兜底文案。"""
    if needs_review:
        return "建议执业医师复核后再行签发。"
    if isinstance(actions, (list, tuple)) and actions:
        return "处置建议由规则表给出（详见「处置建议」段落），请结合临床判断。"
    return "建议结合临床与随访观察。"


def _format_patient_explanation(explanation: Any) -> str:
    """M10 患者解释话术文本（缺失时为占位文本）。"""
    placeholder = "（本次未生成患者解释话术）"
    if not isinstance(explanation, Mapping):
        return placeholder
    lines = []
    if explanation.get("explanation"):
        lines.append(f"解释：{explanation['explanation']}")
    if explanation.get("reassurance"):
        lines.append(f"安抚：{explanation['reassurance']}")
    if explanation.get("next_step"):
        lines.append(f"下一步：{explanation['next_step']}")
    if explanation.get("source_reference"):
        lines.append(f"来源：{explanation['source_reference']}")
    return "\n".join(lines) if lines else placeholder


def build_report_content(result: Any, session: Any = None) -> dict[str, Any]:
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

    # M3/M7：结构化测量与处置建议来自管线写入的 clinical_attributes。
    # 这里按普通 dict 处理（findings 是 JSON），不做模型校验——报告渲染
    # 不该因为某个字段格式问题而失败，缺字段时按"未提供"如实标注。
    clinical = findings_raw.get("clinical_attributes") or {}
    measurement = clinical.get("measurement") or {}
    actions = clinical.get("clinical_actions") or []

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
        "model_version": settings.resolved_model_version,
        # A.52：单独给出**真实生成模型标识**（引擎版本 vs 生成模型，溯源各行其职）
        "llm_model": settings.resolved_llm_model,
        "diagnosis_status": str(getattr(result, "status", "") or ""),
        "confidence": round(confidence, 4),
        # 检查所见：医生提交的临床文本（含语音转写）即最可靠的一手描述
        "findings": clinical_text or "（未提供临床描述）",
        # 诊断意见：融合层输出
        "diagnosis": conclusion or "（模型未产出结论，请人工评估）",
        "recommendation": _format_recommendation(needs_review, actions),
        # M3 测量摘要 / M7 处置建议（器官模板中的独立段落）
        "measurement_summary": _format_measurement(measurement),
        "clinical_actions": _format_actions(actions),
        # 分割旁路摘要（2026-10-03）：轮廓/面积。**未做分割时如实写明原因**，
        # 不留空——空段落会让医生以为"做了分割但没发现东西"。
        "nodule_summary": _format_nodule_segmentation(
            findings_raw.get("segmentation")
        ),
        # M10 患者解释话术：**刻意不放进模板**——报告是医疗文书，面向患者的
        # 安抚性表述混入正文会改变文书性质；由医生按需追加（见 render_report）。
        "patient_explanation": _format_patient_explanation(
            clinical.get("patient_explanation")
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


# M10：患者解释段标题。标注"非正式报告内容"，避免打印后与诊断意见混淆。
PATIENT_EXPLANATION_HEADING = "患者解释（非正式报告内容，需医生确认后方可告知患者）"

# 话术缺失时的占位前缀（用于判断"是否有可追加的内容"）
PATIENT_EXPLANATION_PLACEHOLDER_PREFIX = "（本次未生成"


def render_report(
    content: Mapping[str, Any], *, include_patient_explanation: bool = False
) -> str:
    """按模板渲染报告正文（M8：按器官选择模板）。

    Args:
        content: :func:`build_report_content` 的输出。
        include_patient_explanation: M10 是否追加患者解释段。**默认不追加**：
            报告是医疗文书，面向患者的安抚性表述混入正文会改变文书性质；
            医生选择告知/打印患者版时再显式开启。
    """
    organ = str(content.get("organ") or "") if isinstance(content, Mapping) else ""
    rendered = render_template(load_template(organ=organ), content)
    if not include_patient_explanation:
        return rendered

    explanation = (
        content.get("patient_explanation") if isinstance(content, Mapping) else None
    )
    if not explanation or str(explanation).startswith(PATIENT_EXPLANATION_PLACEHOLDER_PREFIX):
        # 没有话术就不加空段（打印出来一个空的"患者解释"毫无意义）
        return rendered
    return f"{rendered}\n\n## {PATIENT_EXPLANATION_HEADING}\n\n{explanation}\n"


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


def sign_payload(payload: bytes) -> dict[str, str]:
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


async def update_report_sections(
    *,
    report: DiagnosisReport,
    sections: Mapping[str, str],
    report_repo: ReportRepository,
) -> DiagnosisReport:
    """医生补录段落（A.32）——合并进 content、重渲染、重算摘要。

    设计要点：
      * **只允许草稿**。已签名报告的正文由 HSM 签名锁定，改 content 等于
        让签名失效（verify 会拦），必须在状态层就拒绝，而不是让医生撞
        一次验签失败才明白。
      * 只接受 ``SUPPLEMENTABLE_SECTIONS`` 里的键、且值为**非空字符串**：
        补录是"医生写的正文"，不是清空或注入任意结构的通道。
      * 补录的段落记录进 ``content["doctor_supplemented"]``——段落**出处**
        是临床文书的一部分（AI 产出 vs 医生补录），NMPA 体系审核会问。
      * 重渲染后重算 ``content_digest``：摘要必须始终与正文一致，否则
        签名校验会误判"正文被改动"。
    """
    if report.status != "draft":
        raise ValueError(f"only draft reports can be supplemented, got {report.status}")

    content = dict(report.content) if isinstance(report.content, dict) else {}
    applied: list[str] = []
    for key in SUPPLEMENTABLE_SECTIONS:
        value = sections.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        content[key] = text
        applied.append(key)

    if not applied:
        raise ValueError("no supplementable sections provided")

    supplemented = list(content.get("doctor_supplemented") or [])
    for key in applied:
        if key not in supplemented:
            supplemented.append(key)
    content["doctor_supplemented"] = supplemented

    rendered = render_report(content)
    payload = report_payload(rendered)
    updated = await report_repo.update_sections(
        report.report_id,
        content=content,
        rendered=rendered,
        content_digest=compute_digest(payload),
    )
    if updated is None:
        raise ValueError(f"report not found: {report.report_id}")

    logger.info(
        "report_sections_supplemented",
        report_id=report.report_id,
        sections=applied,
        actor_actor="doctor",
    )
    return updated


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
    actor_id: str | None = None,
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
    adapter: PacsPushAdapter | None = None,
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


def list_evidence_ids(report: DiagnosisReport) -> list[str]:
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
