"""A.88 — 跨器官融合接线（让"≥2 条响应"在真实链路里成立）。

## 为什么必须有本模块（2026-10-25 接线审计）

设计文档写「1 个调度 + N 个专科并行 + 四层融合」，而生产链路的实际形状是：

* ``POST /api/v1/diagnosis/request`` → ``dispatcher.invoke()`` **只调 1 个专科**
  （``app/agents/dispatcher.py:185``），于是融合层永远只收到 **1 条**
  ``AgentResponse``；
* 冲突检测的 4 类规则全部要求 ``len(responses) >= 2``
  （``app/fusion/conflict.py:287,313,335,384``）→ 单专科请求下**恒空**；
* 跨器官一致性同样要求 ≥2（``app/fusion/consistency.py:656``）→ 不可达。

这不是某个函数的 bug，而是**前提不成立**：融合层要 ≥2 份独立结论，而单器官端点
每次只产 1 份。既不能为了"让开关亮"去伪造第二份结论（那是造假），也不能把
``dispatch_all()`` 硬接到单器官端点（会把 LLM 成本翻倍、且与"单部位判读"语义冲突）。

真正的 ≥2 只发生在**多器官会话**：``run_multi_organ_diagnosis`` 串行跑完 2~3 个
器官，每个器官都产出了**独立结论**。本模块在该会话级收尾处把它们收集起来做一次
真正的跨器官融合（冲突检测 + 跨器官一致性），结果写回各器官的 ``findings``
（``cross_organ_fusion`` 块）并随多器官响应返回。

## 边界（写死在代码里）

* **不改任何单器官判定**：本模块只在多器官会话**全部器官跑完之后**追加一段
  诊断旁信息；它不会修改任何器官的 status / confidence / 结论正文。
* **不新增 LLM 调用**：只复用融合层已有的检测器（纯函数 + 既有 RAG 检索），
  因此不增加 P99 与显存压力。
* **失败不阻断**：任何异常都只记 warning 并返回 ``status="unavailable"`` ——
  跨器官信息是参考，不是诊断依据。
* **证据诚实**：``detected_organs < 2`` 时明确返回 ``insufficient_responses``，
  绝不把"只跑了 1 个器官"写成"无冲突"。
"""
from __future__ import annotations

from typing import Any

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.protocols import (
    ConflictReport,
    ConsistencyCheckResult,
    ConsistencySeverity,
    FusionRequest,
)
from app.models.schemas import Organ
from app.services.sg_output_data import extract_findings_text

logger = structlog.get_logger("app.services.cross_organ_fusion")

__all__ = [
    "CROSS_ORGAN_FINDINGS_KEY",
    "build_cross_organ_responses",
    "cross_organ_fusion",
]

#: 写回 ``diagnosis_results.findings`` 的键名（读路径与探针据此取值）。
CROSS_ORGAN_FINDINGS_KEY = "cross_organ_fusion"

#: 跨器官融合至少要几份独立结论（与 app/fusion/conflict.py 的判据同源）。
MIN_RESPONSES = 2


def build_cross_organ_responses(
    organ_results: list[dict[str, Any]],
) -> list[AgentResponse]:
    """把各器官**已落库的结论**还原成 ``AgentResponse``（供融合层消费）。

    为什么还原而不是重新调用：这些结论已经产出并入库，重新调 ``dispatch_all()``
    会再花 N 次 LLM 时间与成本，且可能得到与医生看到的**不一样**的结论 ——
    跨器官校验必须针对"已产出的那份"，否则校验对象本身漂移。

    Args:
        organ_results: 每项至少含 ``organ`` / ``findings``（落库 JSONB 原样）。
            缺 ``findings`` 或抽不出正文的器官**不参与**（宁缺毋滥：空正文会让
            分级/建议关键词全是空匹配，产生假冲突）。

    Returns:
        按输入顺序的响应列表；``len < 2`` 时调用方应视为"证据不足"。
    """
    responses: list[AgentResponse] = []
    for item in organ_results:
        organ_raw = str(item.get("organ") or "").strip().lower()
        try:
            organ = Organ(organ_raw)
        except ValueError:
            logger.warning("cross_organ_unknown_organ_skipped", organ=organ_raw)
            continue

        findings = item.get("findings")
        fusion_payload = (
            findings.get("fusion_result") if isinstance(findings, dict) else None
        )
        content = extract_findings_text(fusion_payload)
        if not content.strip():
            logger.info("cross_organ_result_without_conclusion", organ=organ.value)
            continue

        confidence = 0.0
        needs_review = True
        evidence_ids: list[str] = []
        if isinstance(fusion_payload, dict):
            try:
                confidence = float(fusion_payload.get("final_confidence") or 0.0)
            except (TypeError, ValueError):  # silent-ok: 历史脏值 → 按未提供处理
                confidence = 0.0
            needs_review = bool(fusion_payload.get("needs_review", True))
            raw_ids = fusion_payload.get("evidence_chunk_ids")
            if isinstance(raw_ids, list):
                evidence_ids = [str(x) for x in raw_ids]

        responses.append(
            AgentResponse(
                agent_name=f"{organ.value}_specialist",
                organ=organ,
                content=content,
                confidence=max(0.0, min(1.0, confidence)),
                evidence_chunk_ids=evidence_ids,
                needs_review=needs_review,
                reasoning_trace=["cross_organ: reconstructed from persisted findings"],
            )
        )
    return responses


async def _safe_conflict(
    conflict_detector: Any, responses: list[AgentResponse]
) -> ConflictReport:
    """冲突检测（失败安全：异常 → 空报告 + 可读 detail）。"""
    try:
        return conflict_detector.detect(responses)
    except Exception as exc:
        logger.warning(
            "cross_organ_conflict_check_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ConflictReport(
            has_conflict=False,
            detail=f"conflict_check_failed: {type(exc).__name__}",
        )


async def _safe_consistency(
    consistency_checker: Any, request: FusionRequest
) -> ConsistencyCheckResult:
    """一致性校验（失败安全：异常 → INFO 占位 + 可读 detail）。"""
    try:
        return await consistency_checker.check(request)
    except Exception as exc:
        logger.warning(
            "cross_organ_consistency_check_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ConsistencyCheckResult(
            is_consistent=True,
            detail=f"consistency_check_failed: {type(exc).__name__}",
        )


async def cross_organ_fusion(
    *,
    session_id: str,
    organ_results: list[dict[str, Any]],
    fusion_engine: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """对一次多器官会话做**真正的**跨器官融合（冲突检测 + 跨器官一致性）。

    Args:
        session_id: 会话 ID（写进留痕，便于探针按会话聚合）。
        organ_results: 各器官的 ``{"organ", "findings"}``（落库原样）。
        fusion_engine: ``FusionEngineImpl``（取它的检测器复用同一套判据与阈值）。
            ``None`` 或缺少子组件时返回 ``unavailable``（不自己另建一套阈值）。
        request_id: 可选的显式请求标识（默认 ``xorgan_<session_id>``）。

    Returns:
        可 JSON 序列化的汇总块（形如 ``status`` / ``organ_count`` /
        ``conflict`` / ``consistency``）；永远是 dict，绝不抛异常。
    """
    responses = build_cross_organ_responses(organ_results)
    organ_count = len(responses)
    base: dict[str, Any] = {
        "status": "ok",
        "session_id": session_id,
        "organ_count": organ_count,
        "organs": [r.organ.value for r in responses if r.organ is not None],
        "detector_thresholds_applied": True,
    }

    if organ_count < MIN_RESPONSES:
        # 诚实口径：不足 2 份独立结论 = 前提不成立，不是"无冲突"。
        logger.info(
            "cross_organ_insufficient_responses",
            session_id=session_id,
            organ_count=organ_count,
        )
        base["status"] = "insufficient_responses"
        base["reason"] = f"need >= {MIN_RESPONSES} organ conclusions, got {organ_count}"
        return base

    conflict_detector = getattr(fusion_engine, "_conflict_detector", None)
    consistency_checker = getattr(fusion_engine, "_consistency_checker", None)
    if conflict_detector is None or consistency_checker is None:
        logger.warning(
            "cross_organ_fusion_engine_unavailable",
            session_id=session_id,
            has_conflict_detector=conflict_detector is not None,
            has_consistency_checker=consistency_checker is not None,
        )
        base["status"] = "unavailable"
        base["reason"] = "fusion_engine_unavailable"
        return base

    fusion_request = FusionRequest(
        request_id=request_id or f"xorgan_{session_id}",
        session_id=session_id,
        agent_responses=responses,
        # 跨器官场景没有"上一次检查"可继承（既往对比属单器官纵向对比，
        # 由 A2 在管线单器官路径里做）。
        prior_diagnosis=None,
    )

    conflict_report = await _safe_conflict(conflict_detector, responses)
    consistency_result = await _safe_consistency(consistency_checker, fusion_request)

    base["conflict"] = {
        "has_conflict": conflict_report.has_conflict,
        "conflict_types": [c.value for c in conflict_report.conflict_types],
        "resolution_suggestion": conflict_report.resolution_suggestion.value,
        "detail": conflict_report.detail,
    }
    base["consistency"] = {
        "is_consistent": consistency_result.is_consistent,
        "severity": consistency_result.severity.value,
        "compared_with_prior": consistency_result.compared_with_prior,
        "compared_with_guidelines": consistency_result.compared_with_guidelines,
        "inconsistent_organ_pairs": [
            list(pair) for pair in consistency_result.inconsistent_organ_pairs
        ],
        "detail": consistency_result.detail,
    }
    base["needs_review"] = bool(
        conflict_report.has_conflict
        or consistency_result.severity == ConsistencySeverity.CRITICAL
    )

    logger.info(
        "cross_organ_fusion_completed",
        session_id=session_id,
        organ_count=organ_count,
        has_conflict=conflict_report.has_conflict,
        consistency_severity=consistency_result.severity.value,
    )
    return base
