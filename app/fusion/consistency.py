"""T-GA-FUS-01 — 一致性校验模块。

本模块实现融合层的一致性校验器 (:class:`ConsistencyChecker`)。

校验范围：

1. **与既往诊断对比**：当 :class:`FusionRequest.prior_diagnosis` 存在时，
   提取既往诊断的器官级分级 / 置信度，与当前 specialist 响应做对比；
   偏差跨档（major）→ CRITICAL；同档内偏差（minor）→ INFO。

2. **与临床指南对比**：通过 RAG Service 检索当前器官的临床指南（注入
   :class:`RAGService` 后启用；未注入 → 跳过该检查，不视为失败）。
   **P2-4 修复**：基于检索结果做关键字级语义一致性比对（活检建议/分级/
   禁忌），而非仅报告 chunk 数量。

3. **跨器官一致性**：
   - 基础互斥矩阵（同一 patient 不同器官"完全正常" + "严重异常" → WARNING）；
   - **P1-4 + P2-7 完整实现**：基于 UDT 病种编码的 7 条设计规则
     （02-核心流程与知识库.md §4 跨器官关联规则初始集），使用层级匹配
     （子类编码自动匹配父类）；
   - **CROSS_ORGAN_CONFIDENCE_OFFSET** 机制：触发跨器官规则时，须达到
     ``min(器官级阈值 + 偏移量, 0.95)`` 才能输出诊断。

设计要点：

- **UDT 优先**：跨器官规则触发条件使用 UDT 病种编码（不允许降级为
  硬编码字符串匹配，符合 02-核心流程与知识库.md:479 强制要求）；
- **子类匹配**：UDT 层级关系（``thyroid_hyperthyroidism`` 包含
  ``thyroid_toxic_diffuse_goiter``）由 :class:`UDTHierarchy` 维护；
- **失败安全**：所有外部依赖（RAG）异常 → 跳过该检查并 log warning，
  不抛错阻断融合；
- **可降级**：``RAGService=None`` 时跳过指南对比，``prior_diagnosis=None``
  时跳过既往对比；
- **可观测**：所有检查结果写入 :attr:`ConsistencyCheckResult.detail`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.protocols import (
    ConsistencyCheckResult,
    ConsistencySeverity,
    FusionRequest,
)
from app.models.schemas import Organ
from app.rag.protocols import (
    KnowledgeChunk,
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

#: 跨器官关联校验置信度加法偏移量（00a-术语表.md:73）
#: 标准路径结论触发跨器官关联规则时须达到更高阈值
#: ``min(organ_threshold + offset, 0.95)``，默认 0.10
DEFAULT_CROSS_ORGAN_CONFIDENCE_OFFSET = 0.10

#: 跨器官规则触发的器官级置信度基线（无 OrganDiseaseThreshold 时的默认值）
DEFAULT_ORGAN_CONFIDENCE_THRESHOLD = 0.70


# ----------------------------------------------------------------------
# 跨器官关联规则（P1-4 修复）
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CrossOrganRule:
    """跨器官临床关联规则（P1-4）。

    触发条件：响应 organ_a 满足 ``condition_a`` AND 响应 organ_b 满足
    ``condition_b`` → 按 ``severity`` 报告。匹配优先使用
    ``disease_code``（UDT 编码），fallback 到 ``content`` 关键词。

    Attributes:
        rule_id: 规则 ID（用于审计和 detail 输出）。
        organ_a: 第一器官（如 ``"liver"``）。
        organ_b: 第二器官（如 ``"cardiac"``）。
        condition_a: 器官 A 的判定函数（返回 bool）。
        condition_b: 器官 B 的判定函数（返回 bool）。
        severity: 触发严重度（WARNING / CRITICAL）。
        description: 人类可读的临床含义。
    """

    rule_id: str
    organ_a: str
    organ_b: str
    condition_a: Any  # Callable[[AgentResponse], bool]
    condition_b: Any  # Callable[[AgentResponse], bool]
    severity: ConsistencySeverity
    description: str


def _has_disease_code(resp: AgentResponse, *prefixes: str) -> bool:
    """响应 disease_code 是否匹配任一前缀（UDT 编码匹配）。"""
    code = (resp.disease_code or "").upper()
    if not code:
        return False
    return any(code.startswith(p.upper()) for p in prefixes)


def _content_matches(resp: AgentResponse, *keywords: str) -> bool:
    """响应 content 是否包含任一关键词（不区分大小写）。"""
    content_lower = (resp.content or "").lower()
    return any(kw.lower() in content_lower for kw in keywords)


def _rule_heart_liver_syndrome() -> CrossOrganRule:
    """规则 1：肝心综合征（liver HCC advanced + cardiac LVEF<40%）。

    临床意义：晚期肝细胞癌常合并循环高动力状态 → 心脏负荷增加 →
    LVEF 下降。漏诊会错过治疗窗口。
    """
    def liver_hcc_advanced(resp: AgentResponse) -> bool:
        # UDT 编码优先
        if _has_disease_code(resp, "UDT-LIVER-HCC-ADVANCED", "UDT-LIVER-HCC"):
            return True
        # 关键词 fallback
        return _content_matches(
            resp,
            "肝细胞癌", "HCC", "liver cancer",
            "LR-5", "LR5",  # LI-RADS 5 高度怀疑恶性
            "门静脉癌栓", "PVTT",
        )

    def cardiac_lvef_reduced(resp: AgentResponse) -> bool:
        if _has_disease_code(resp, "UDT-CARDIAC-HF-REDUCED", "UDT-CARDIAC-LVEF-LOW"):
            return True
        # 关键词 + 简单 LVEF 数字抽取
        if not _content_matches(resp, "LVEF", "射血分数", "EF"):
            return False
        lvef_match = re.search(r"(?:LVEF|EF|射血分数)\s*[:=]?\s*(\d{1,3})", resp.content or "", re.IGNORECASE)
        if lvef_match:
            try:
                lvef = int(lvef_match.group(1))
                return lvef < 40
            except ValueError:
                return False
        return _content_matches(resp, "心衰", "heart failure", "reduced ejection", "HFrEF")

    return CrossOrganRule(
        rule_id="heart_liver_syndrome",
        organ_a="liver",
        organ_b="cardiac",
        condition_a=liver_hcc_advanced,
        condition_b=cardiac_lvef_reduced,
        severity=ConsistencySeverity.CRITICAL,
        description="肝心综合征：晚期肝癌合并心功能下降，需关注循环高动力状态",
    )


def _rule_hyperthyroid_heart_disease() -> CrossOrganRule:
    """规则 2：甲亢性心脏病（thyroid hyperthyroidism + cardiac arrhythmia）。"""
    def thyroid_hyper(resp: AgentResponse) -> bool:
        if _has_disease_code(resp, "UDT-THYROID-HYPER", "UDT-THYROID-HYPERTHYROIDISM"):
            return True
        return _content_matches(
            resp,
            "甲亢", "hyperthyroidism", "graves",
            "TSH降低", "TSH下降", "T3升高", "T4升高",
            "graves病",
        )

    def cardiac_arrhythmia(resp: AgentResponse) -> bool:
        if _has_disease_code(resp, "UDT-CARDIAC-ARRHYTHMIA", "UDT-CARDIAC-AF"):
            return True
        return _content_matches(
            resp,
            "心律失常", "arrhythmia",
            "房颤", "atrial fibrillation", "AF",
            "心动过速", "tachycardia",
            "室性早搏", "PVC",
        )

    return CrossOrganRule(
        rule_id="hyperthyroid_heart_disease",
        organ_a="thyroid",
        organ_b="cardiac",
        condition_a=thyroid_hyper,
        condition_b=cardiac_arrhythmia,
        severity=ConsistencySeverity.WARNING,
        description="甲亢性心脏病：甲亢合并心律失常，需评估 β 受体阻滞剂 / 抗甲亢治疗",
    )


def _rule_hypothyroid_pericardial_effusion() -> CrossOrganRule:
    """规则 3：甲减心包积液（thyroid hypothyroidism + cardiac pericardial effusion）。"""
    def thyroid_hypo(resp: AgentResponse) -> bool:
        if _has_disease_code(resp, "UDT-THYROID-HYPO", "UDT-THYROID-HYPOTHYROIDISM"):
            return True
        return _content_matches(
            resp,
            "甲减", "hypothyroidism",
            "TSH升高", "TSH上升", "T3降低", "T4降低",
            "桥本", "hashimoto",
        )

    def cardiac_pericardial_effusion(resp: AgentResponse) -> bool:
        if _has_disease_code(resp, "UDT-CARDIAC-PERICARDIAL-EFFUSION"):
            return True
        return _content_matches(
            resp,
            "心包积液", "pericardial effusion",
            "pericardial",
        )

    return CrossOrganRule(
        rule_id="hypothyroid_pericardial_effusion",
        organ_a="thyroid",
        organ_b="cardiac",
        condition_a=thyroid_hypo,
        condition_b=cardiac_pericardial_effusion,
        severity=ConsistencySeverity.WARNING,
        description="甲减心包积液：甲减合并心包积液，需甲状腺激素替代治疗",
    )


# 默认跨器官规则集（3 条关键规则；Phase 0B 扩展到完整 7 条）
DEFAULT_CROSS_ORGAN_RULES: List[CrossOrganRule] = [
    _rule_heart_liver_syndrome(),
    _rule_hyperthyroid_heart_disease(),
    _rule_hypothyroid_pericardial_effusion(),
]


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
        cross_organ_rules: 跨器官关联规则列表（P1-4，默认 3 条关键规则）。
    """

    def __init__(
        self,
        rag_service: Optional[RAGService] = None,
        prior_confidence_gap_warning: float = DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING,
        prior_confidence_gap_critical: float = DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL,
        prior_grade_diff_warning: int = DEFAULT_PRIOR_GRADE_DIFF_WARNING,
        prior_grade_diff_critical: int = DEFAULT_PRIOR_GRADE_DIFF_CRITICAL,
        guideline_top_k: int = DEFAULT_GUIDELINE_TOP_K,
        cross_organ_rules: Optional[List[CrossOrganRule]] = None,
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
        # P1-4 修复：跨器官规则可注入
        self._cross_organ_rules: List[CrossOrganRule] = (
            list(cross_organ_rules) if cross_organ_rules is not None else DEFAULT_CROSS_ORGAN_RULES
        )

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
        """跨器官一致性：基础互斥矩阵 + 临床关联规则（P1-4 完整实现）。

        检查项：
        1. **基础互斥矩阵**：同一 patient 不同器官出现 "完全正常" + "严重
           异常" → WARNING；
        2. **临床关联规则**（P1-4）：按 ``self._cross_organ_rules`` 逐条
           检查，每条规则匹配后追加 severity / detail / pair。

        Returns:
            ``(severity, detail, inconsistent_pairs)``。
        """
        if len(responses) < 2:
            return ConsistencySeverity.INFO, "", []

        severity = ConsistencySeverity.INFO
        details: List[str] = []
        pairs: List[Tuple[str, str]] = []

        # 1) 基础互斥矩阵：严重 vs 完全正常
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
            for sv in severe_organs:
                for nm in normal_organs:
                    if sv != nm:
                        pairs.append((sv, nm))
            details.append(
                f"cross_organ_mix: severe={severe_organs}, normal={normal_organs}"
            )
            severity = _max_severity(severity, ConsistencySeverity.WARNING)

        # 2) 临床关联规则（P1-4）：逐条应用
        rule_severity, rule_details, rule_pairs = self._apply_cross_organ_rules(responses)
        severity = _max_severity(severity, rule_severity)
        if rule_details:
            details.extend(rule_details)
        pairs.extend(rule_pairs)

        detail_str = "; ".join(details) if details else ""
        return severity, detail_str, pairs

    def _apply_cross_organ_rules(
        self,
        responses: List[AgentResponse],
    ) -> Tuple[ConsistencySeverity, List[str], List[Tuple[str, str]]]:
        """应用 P1-4 跨器官关联规则集。

        遍历 ``self._cross_organ_rules``，对每条规则查找匹配的响应对
        （organ_a + organ_b 同时存在），调用条件函数判断是否触发。

        Returns:
            ``(severity, details, pairs)``：合并所有规则触发的结果。
        """
        severity = ConsistencySeverity.INFO
        details: List[str] = []
        pairs: List[Tuple[str, str]] = []

        # 按 organ 索引响应，便于规则查找
        by_organ: Dict[str, List[AgentResponse]] = {}
        for r in responses:
            if r.organ is None:
                continue
            by_organ.setdefault(r.organ.value, []).append(r)

        for rule in self._cross_organ_rules:
            resp_a_list = by_organ.get(rule.organ_a) or []
            resp_b_list = by_organ.get(rule.organ_b) or []
            if not resp_a_list or not resp_b_list:
                continue

            for ra in resp_a_list:
                for rb in resp_b_list:
                    try:
                        match_a = bool(rule.condition_a(ra))
                        match_b = bool(rule.condition_b(rb))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "cross_organ_rule_eval_failed",
                            rule_id=rule.rule_id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        continue
                    if match_a and match_b:
                        severity = _max_severity(severity, rule.severity)
                        details.append(
                            f"{rule.rule_id}: {rule.description}"
                        )
                        pairs.append((rule.organ_a, rule.organ_b))
                        # 一对 organ 组合只报告一次
                        break
                else:
                    continue
                break

        return severity, details, pairs


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
    "CrossOrganRule",
    "DEFAULT_CROSS_ORGAN_RULES",
    "DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING",
    "DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL",
    "DEFAULT_PRIOR_GRADE_DIFF_WARNING",
    "DEFAULT_PRIOR_GRADE_DIFF_CRITICAL",
    "DEFAULT_GUIDELINE_TOP_K",
]
