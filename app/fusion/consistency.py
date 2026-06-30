"""T-GA-FUS-01 — 一致性校验模块。

本模块实现融合层的一致性校验器 (:class:`ConsistencyChecker`)。

校验范围：

1. **与既往诊断对比**：当 :class:`FusionRequest.prior_diagnosis` 存在时，
   提取既往诊断的器官级分级 / 置信度，与当前 specialist 响应做对比；
   偏差跨档（major）→ CRITICAL；同档内偏差（minor）→ INFO。

2. **与临床指南对比**：通过 RAG Service 检索当前器官的临床指南（注入
   :class:`RAGService` 后启用；未注入 → 跳过该检查，不视为失败）。

3. **跨器官一致性**：检查不同器官的诊断结果是否互斥。例如：
   - 心脏 LVEF<30%（严重心衰）通常不与"高强度运动"互斥，但与
     "完全正常"互斥；
   - 当前 Phase 0A 仅做基础对比：同一 patient 不同器官出现
     "完全正常 + 严重异常" → WARNING；
   - 真正的临床互斥矩阵（基于「疾病共病禁忌」）放 Phase 0B+。

设计要点：

- **失败安全**：所有外部依赖（RAG）异常 → 跳过该检查并 log warning，
  不抛错阻断融合；
- **可降级**：``RAGService=None`` 时跳过指南对比，``prior_diagnosis=None``
  时跳过既往对比；
- **可观测**：所有检查结果写入 :attr:`ConsistencyCheckResult.detail`。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.protocols import (
    ConsistencyCheckResult,
    ConsistencySeverity,
    FusionRequest,
)
from app.models.schemas import Organ
from app.rag.protocols import (
    RAGService,
    RetrievalMethod,
    RetrievalRequest,
)

logger = structlog.get_logger("app.fusion.consistency")


# ----------------------------------------------------------------------
# 默认阈值
# ----------------------------------------------------------------------

#: 既往诊断与当前 confidence 差距 → WARNING 的阈值
DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING = 0.3

#: 既往诊断与当前 confidence 差距 → CRITICAL 的阈值
DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL = 0.5

#: 既往诊断分级码差异容忍度（数字差 ≤ 该值 → INFO；否则 WARNING / CRITICAL）
DEFAULT_PRIOR_GRADE_DIFF_WARNING = 1
DEFAULT_PRIOR_GRADE_DIFF_CRITICAL = 2

#: 临床指南查询默认 top_k
DEFAULT_GUIDELINE_TOP_K = 3


# ----------------------------------------------------------------------
# ConsistencyChecker
# ----------------------------------------------------------------------


class ConsistencyChecker:
    """融合层一致性校验器（T-GA-FUS-01）。

    Args:
        rag_service: RAG Service（用于临床指南对比）。``None`` 时跳过
            指南对比。
        prior_confidence_gap_warning: 既往诊断 vs 当前 confidence 差距
            触发 WARNING 的阈值。
        prior_confidence_gap_critical: 既往诊断 vs 当前 confidence 差距
            触发 CRITICAL 的阈值。
        prior_grade_diff_warning: 既往诊断 vs 当前分级数字差触发 WARNING
            的阈值。
        prior_grade_diff_critical: 既往诊断 vs 当前分级数字差触发 CRITICAL
            的阈值。
        guideline_top_k: 临床指南 RAG 检索 top_k。
    """

    def __init__(
        self,
        rag_service: Optional[RAGService] = None,
        prior_confidence_gap_warning: float = DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING,
        prior_confidence_gap_critical: float = DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL,
        prior_grade_diff_warning: int = DEFAULT_PRIOR_GRADE_DIFF_WARNING,
        prior_grade_diff_critical: int = DEFAULT_PRIOR_GRADE_DIFF_CRITICAL,
        guideline_top_k: int = DEFAULT_GUIDELINE_TOP_K,
    ) -> None:
        if prior_confidence_gap_critical < prior_confidence_gap_warning:
            raise ValueError(
                "prior_confidence_gap_critical must be >= prior_confidence_gap_warning"
            )
        if prior_grade_diff_critical < prior_grade_diff_warning:
            raise ValueError(
                "prior_grade_diff_critical must be >= prior_grade_diff_warning"
            )
        if guideline_top_k <= 0:
            raise ValueError("guideline_top_k must be positive")

        self._rag_service = rag_service
        self._prior_confidence_gap_warning = prior_confidence_gap_warning
        self._prior_confidence_gap_critical = prior_confidence_gap_critical
        self._prior_grade_diff_warning = prior_grade_diff_warning
        self._prior_grade_diff_critical = prior_grade_diff_critical
        self._guideline_top_k = guideline_top_k

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def check(self, request: FusionRequest) -> ConsistencyCheckResult:
        """执行一致性校验。

        Args:
            request: 融合请求（含 specialist 响应 + 既往诊断 + 临床上下文）。

        Returns:
            :class:`ConsistencyCheckResult`：一致性校验结果。``is_consistent``
            为 True 当且仅当 ``severity != CRITICAL``。
        """
        details: List[str] = []
        inconsistent_pairs: List[Tuple[str, str]] = []
        severity = ConsistencySeverity.INFO
        compared_with_prior = False
        compared_with_guidelines = False

        responses = request.agent_responses or []

        # 1) 与既往诊断对比
        if request.prior_diagnosis is not None:
            compared_with_prior = True
            prior_severity, prior_detail, prior_pairs = self._compare_with_prior(
                responses, request.prior_diagnosis
            )
            severity = _max_severity(severity, prior_severity)
            if prior_detail:
                details.append(prior_detail)
            inconsistent_pairs.extend(prior_pairs)

        # 2) 与临床指南对比（通过 RAG）
        if self._rag_service is not None and responses:
            try:
                compared_with_guidelines = True
                guideline_severity, guideline_detail, guideline_pairs = (
                    await self._compare_with_guidelines(responses, request)
                )
                severity = _max_severity(severity, guideline_severity)
                if guideline_detail:
                    details.append(guideline_detail)
                inconsistent_pairs.extend(guideline_pairs)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "consistency_guideline_check_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                details.append(f"guideline_check_skipped: {type(exc).__name__}")
        elif self._rag_service is not None:
            # 有 RAG 但无响应 → 仍标记 compared_with_guidelines=False
            compared_with_guidelines = False

        # 3) 跨器官一致性（基础互斥矩阵）
        if len(responses) >= 2:
            cross_severity, cross_detail, cross_pairs = self._check_cross_organ(
                responses
            )
            severity = _max_severity(severity, cross_severity)
            if cross_detail:
                details.append(cross_detail)
            inconsistent_pairs.extend(cross_pairs)

        # 构造最终结果
        is_consistent = severity != ConsistencySeverity.CRITICAL
        detail_str = "; ".join(details) if details else "consistency check passed"
        return ConsistencyCheckResult(
            is_consistent=is_consistent,
            severity=severity,
            inconsistent_organ_pairs=inconsistent_pairs,
            compared_with_prior=compared_with_prior,
            compared_with_guidelines=compared_with_guidelines,
            detail=detail_str,
        )

    # ------------------------------------------------------------------
    # 内部：与既往诊断对比
    # ------------------------------------------------------------------

    def _compare_with_prior(
        self,
        responses: List[AgentResponse],
        prior: Dict[str, Any],
    ) -> Tuple[ConsistencySeverity, str, List[Tuple[str, str]]]:
        """与既往诊断对比。

        既往诊断格式约定（``prior_diagnosis`` dict）：
        ```
        {
            "organ": "thyroid" | "liver" | "cardiac",
            "grade": "TR4" | "LR-5" | "LVEF40" | ...,
            "confidence": 0.85,
        }
        ```

        偏差判定：
        - confidence 差距 ≥ critical → CRITICAL
        - confidence 差距 ≥ warning → WARNING
        - 分级数字差 ≥ critical → CRITICAL
        - 分级数字差 ≥ warning → WARNING
        - 否则 → INFO
        """
        if not responses:
            return (
                ConsistencySeverity.INFO,
                "no responses to compare with prior",
                [],
            )

        prior_organ = str(prior.get("organ", "")).strip().lower()
        prior_grade = str(prior.get("grade", "")).strip()
        prior_conf_raw = prior.get("confidence")
        try:
            prior_confidence = float(prior_conf_raw) if prior_conf_raw is not None else None
        except (TypeError, ValueError):
            prior_confidence = None

        if not prior_organ and not prior_grade and prior_confidence is None:
            return (
                ConsistencySeverity.INFO,
                "prior_diagnosis is empty; skipping prior comparison",
                [],
            )

        # 找到同器官的 specialist 响应
        matching = [r for r in responses if r.organ and r.organ.value == prior_organ]
        if not matching:
            return (
                ConsistencySeverity.INFO,
                f"no current response for prior organ={prior_organ or 'unknown'}",
                [],
            )

        worst_severity = ConsistencySeverity.INFO
        details: List[str] = []
        pairs: List[Tuple[str, str]] = []
        for resp in matching:
            # confidence 差距
            if prior_confidence is not None:
                gap = abs(resp.confidence - prior_confidence)
                if gap >= self._prior_confidence_gap_critical:
                    worst_severity = _max_severity(
                        worst_severity, ConsistencySeverity.CRITICAL
                    )
                    details.append(
                        f"prior_confidence_gap_critical: prior={prior_confidence:.4f}, "
                        f"current={resp.confidence:.4f}, gap={gap:.4f}"
                    )
                elif gap >= self._prior_confidence_gap_warning:
                    worst_severity = _max_severity(
                        worst_severity, ConsistencySeverity.WARNING
                    )
                    details.append(
                        f"prior_confidence_gap_warning: prior={prior_confidence:.4f}, "
                        f"current={resp.confidence:.4f}, gap={gap:.4f}"
                    )
            # 分级码差异
            if prior_grade:
                current_grade = _extract_simple_grade(resp.content or "")
                if current_grade and prior_grade:
                    diff = _grade_diff(prior_grade, current_grade)
                    if diff is not None:
                        if diff >= self._prior_grade_diff_critical:
                            worst_severity = _max_severity(
                                worst_severity, ConsistencySeverity.CRITICAL
                            )
                            details.append(
                                f"prior_grade_diff_critical: prior={prior_grade}, "
                                f"current={current_grade}, diff={diff}"
                            )
                            pairs.append((prior_organ, prior_organ))
                        elif diff >= self._prior_grade_diff_warning:
                            worst_severity = _max_severity(
                                worst_severity, ConsistencySeverity.WARNING
                            )
                            details.append(
                                f"prior_grade_diff_warning: prior={prior_grade}, "
                                f"current={current_grade}, diff={diff}"
                            )
                            pairs.append((prior_organ, prior_organ))

        detail_str = (
            "prior comparison: " + "; ".join(details) if details else "prior comparison ok"
        )
        return worst_severity, detail_str, pairs

    # ------------------------------------------------------------------
    # 内部：与临床指南对比
    # ------------------------------------------------------------------

    async def _compare_with_guidelines(
        self,
        responses: List[AgentResponse],
        request: FusionRequest,
    ) -> Tuple[ConsistencySeverity, str, List[Tuple[str, str]]]:
        """与临床指南对比：通过 RAG 检索当前器官的指南条目。

        简化策略：仅当 RAG 返回空结果时报告 INFO（"no guideline chunks
        retrieved"），不做语义级断言。真正基于指南条目的对比放 Phase 0B+。
        """
        if not responses:
            return ConsistencySeverity.INFO, "", []

        worst_severity = ConsistencySeverity.INFO
        details: List[str] = []
        pairs: List[Tuple[str, str]] = []

        for resp in responses:
            if not resp.organ:
                continue
            organ_value = resp.organ.value
            query_text = (
                f"{organ_value} ultrasound clinical guideline for "
                f"{(resp.content or '')[:100]}"
            )
            try:
                retrieval_request = RetrievalRequest(
                    query=query_text,
                    organ=organ_value,
                    top_k=self._guideline_top_k,
                    use_query_enhancement=False,
                    use_reranking=False,
                    use_bm25_fallback=False,
                    use_multimodal=False,
                    request_id=request.request_id or None,
                    session_id=request.session_id or None,
                )
                result = await self._rag_service.retrieve(retrieval_request)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "consistency_rag_retrieval_failed",
                    organ=organ_value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                details.append(
                    f"guideline_rag_failed[{organ_value}]: {type(exc).__name__}"
                )
                continue
            if not result.chunks:
                details.append(
                    f"no_guideline_chunks[{organ_value}]: retrieval returned empty"
                )
                continue
            # Phase 0A 占位：仅报告 "guideline chunks found"（不展开语义比对）
            details.append(
                f"guideline_chunks_found[{organ_value}]: {len(result.chunks)} chunk(s)"
            )

        detail_str = (
            "guideline comparison: " + "; ".join(details) if details else "guideline comparison skipped"
        )
        return worst_severity, detail_str, pairs

    # ------------------------------------------------------------------
    # 内部：跨器官一致性
    # ------------------------------------------------------------------

    def _check_cross_organ(
        self,
        responses: List[AgentResponse],
    ) -> Tuple[ConsistencySeverity, str, List[Tuple[str, str]]]:
        """跨器官一致性：基础互斥矩阵。

        Phase 0A 简化规则：
        - 同一 patient 不同器官出现 "完全正常" + "严重异常" → WARNING；
        - 真正的临床互斥矩阵（基于「共病禁忌」）放 Phase 0B+。

        Returns:
            ``(severity, detail, inconsistent_pairs)``。
        """
        if len(responses) < 2:
            return ConsistencySeverity.INFO, "", []

        # 简化的"严重异常"识别：confidence > 0.9 且 content 含"严重" / "critical"
        severe_keywords = ("严重", "critical", "malignant", "恶性", "重度")
        normal_keywords = ("正常", "normal", "no abnormality", "未见异常")

        severe_organs: List[str] = []
        normal_organs: List[str] = []
        for r in responses:
            if not r.organ:
                continue
            content_lower = (r.content or "").lower()
            if any(kw.lower() in content_lower for kw in severe_keywords) and r.confidence > 0.85:
                severe_organs.append(r.organ.value)
            if any(kw.lower() in content_lower for kw in normal_keywords) and r.confidence > 0.85:
                normal_organs.append(r.organ.value)

        if severe_organs and normal_organs and set(severe_organs) != set(normal_organs):
            pairs: List[Tuple[str, str]] = []
            for sv in severe_organs:
                for nm in normal_organs:
                    if sv != nm:
                        pairs.append((sv, nm))
            if pairs:
                detail = (
                    f"cross_organ_mix: severe={severe_organs}, normal={normal_organs}"
                )
                return ConsistencySeverity.WARNING, detail, pairs
        return ConsistencySeverity.INFO, "", []


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def _max_severity(a: ConsistencySeverity, b: ConsistencySeverity) -> ConsistencySeverity:
    """返回 ``a`` / ``b`` 中较严重的那个。"""
    order = {
        ConsistencySeverity.INFO: 0,
        ConsistencySeverity.WARNING: 1,
        ConsistencySeverity.CRITICAL: 2,
    }
    return a if order[a] >= order[b] else b


_GRADE_NUM_RE = re.compile(r"(\d+)")


def _extract_simple_grade(text: str) -> Optional[str]:
    """从文本中抽取「数字」形式分级（最后出现的数字）。"""
    if not text:
        return None
    matches = _GRADE_NUM_RE.findall(text)
    if not matches:
        return None
    return matches[-1]


def _grade_diff(prior: str, current: str) -> Optional[int]:
    """两个分级码的数字差（绝对值）。无法解析 → 返回 None。"""
    p = _extract_simple_grade(prior)
    c = _extract_simple_grade(current)
    if p is None or c is None:
        return None
    try:
        return abs(int(p) - int(c))
    except ValueError:
        return None


__all__ = [
    "ConsistencyChecker",
    "DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING",
    "DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL",
    "DEFAULT_PRIOR_GRADE_DIFF_WARNING",
    "DEFAULT_PRIOR_GRADE_DIFF_CRITICAL",
    "DEFAULT_GUIDELINE_TOP_K",
]
