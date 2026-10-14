"""T-GA-FUS-01 — 冲突检测模块。

本模块实现融合层的核心组件之一：冲突检测器 (:class:`ConflictDetector`)。

冲突类型（与 :class:`app.fusion.protocols.ConflictType` 一一对应）：

1. **GRADING_CONFLICT** — 同器官不同专科给出不同分级（如 thyroid 的
   TR3 vs TR4 / liver 的 LR-3 vs LR-5 / cardiac 的 LVEF 40% vs 25%）。
2. **CONFIDENCE_CONFLICT** — 多个响应 confidence 差距 > 0.4
   （默认阈值，可由 :class:`ConflictDetector` 构造参数调整）。
3. **RECOMMENDATION_CONFLICT** — 后续处置建议互相矛盾（活检 vs 随访 /
   立即手术 vs 观察）。
4. **EVIDENCE_CONFLICT** — 引用的知识库条目互相矛盾（基于 chunk
   ``content`` 关键词级别的否定关系检测）。

设计要点：

- **纯函数 / 无状态**：除构造期可注入阈值外，:meth:`detect` 不持有任何
  状态 → 易于测试和并发复用。
- **失败安全**：所有匹配失败（正则错误等）均吞掉并跳过该条规则，**不**
  抛错阻塞融合流程。
- **可观测性**：检出冲突时输出 ``detail`` 字段，便于审计日志。
- **可调阈值**：置信度差距、关键词集、推荐映射表均通过构造参数注入。
"""
from __future__ import annotations

import re
from collections import defaultdict

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.protocols import (
    ConflictReport,
    ConflictResolution,
    ConflictType,
)

logger = structlog.get_logger("app.fusion.conflict")


# ----------------------------------------------------------------------
# 默认阈值 / 模式表
# ----------------------------------------------------------------------

#: 置信度差距默认阈值（最大 - 最小 > 阈值 → CONFIDENCE_CONFLICT）
DEFAULT_CONFIDENCE_GAP_THRESHOLD = 0.4

#: 分级抽取正则（覆盖三器官：thyroid TI-RADS / liver LI-RADS / cardiac LVEF）
# 优先级：先匹配完整 "TI-RADS N" / "LI-RADS N" 形式，再 fallback 到简化 "TR N" / "LR N"
_GRADING_PATTERNS: dict[str, tuple[re.Pattern, str]] = {
    "thyroid": (
        re.compile(
            r"\bT[\s\-_]?I[\s\-_]?R[\s\-_]?A[\s\-_]?D[\s\-_]?S?\s*[\-:]?\s*([1-5])\b"
            r"|\bTR\s*[\-:]?\s*([1-5])\b",
            re.IGNORECASE,
        ),
        "TR{}",
    ),
    "liver": (
        re.compile(
            r"\bL[\s\-_]?I[\s\-_]?R[\s\-_]?A[\s\-_]?D[\s\-_]?S?\s*[\-:]?\s*([1-5])\b"
            r"|\bLR\s*[\-:]?\s*([1-5])\b",
            re.IGNORECASE,
        ),
        "LR-{}",
    ),
    "cardiac": (
        re.compile(r"\bLVEF\s*[:=]?\s*(\d{1,3})\s*%?", re.IGNORECASE),
        "LVEF{}",
    ),
}

#: 处置建议分类（互斥组 → 同器官内出现多组即 RECOMMENDATION_CONFLICT）
_BIOPSY_TOKENS = (
    "活检",
    "biopsy",
    "穿刺",
    "fna",
    "立即手术",
    "surgery",
)
_FOLLOWUP_TOKENS = (
    "随访",
    "观察",
    "followup",
    "follow-up",
    "watchful waiting",
)
_URGENT_TOKENS = (
    "立即",
    "急诊",
    "urgent",
    "emergent",
    "asap",
)
_RECOMMENDATION_GROUPS: list[tuple[str, ...]] = [
    _BIOPSY_TOKENS,
    _FOLLOWUP_TOKENS,
    _URGENT_TOKENS,
]

#: 证据矛盾关键词对（"X 不是 Y" / "X 不是 Y" / "X not Y"）
_NEGATION_PATTERNS = [
    re.compile(r"不是\s*(\S+)"),
    re.compile(r"并非\s*(\S+)"),
    re.compile(r"排除\s*(\S+)"),
    re.compile(r"\bnot\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bno\s+(\w+)", re.IGNORECASE),
    re.compile(r"\bexcludes?\s+(\w+)", re.IGNORECASE),
    re.compile(r"\brules?\s+out\s+(\w+)", re.IGNORECASE),
]


# ----------------------------------------------------------------------
# ConflictDetector
# ----------------------------------------------------------------------


class ConflictDetector:
    """融合层冲突检测器（T-GA-FUS-01）。

    Args:
        confidence_gap_threshold: 触发 :attr:`ConflictType.CONFIDENCE_CONFLICT`
            的最大/最小 confidence 差距。默认 0.4。
        recommendation_groups: 互斥的处置建议组（默认内置三组：活检/随访/紧急）。
        grading_extractors: 可选自定义分级抽取 ``{organ_value: (pattern, fmt)}``
            （默认覆盖 thyroid / liver / cardiac）。
    """

    def __init__(
        self,
        confidence_gap_threshold: float = DEFAULT_CONFIDENCE_GAP_THRESHOLD,
        recommendation_groups: list[tuple[str, ...]] | None = None,
        grading_extractors: dict[str, tuple[re.Pattern, str]] | None = None,
    ) -> None:
        if not (0.0 <= confidence_gap_threshold <= 1.0):
            raise ValueError(
                f"confidence_gap_threshold must be in [0.0, 1.0], got {confidence_gap_threshold}"
            )
        self._confidence_gap_threshold = confidence_gap_threshold
        self._recommendation_groups: list[tuple[str, ...]] = (
            list(recommendation_groups) if recommendation_groups else _RECOMMENDATION_GROUPS
        )
        self._grading_extractors: dict[str, tuple[re.Pattern, str]] = (
            dict(grading_extractors) if grading_extractors else _GRADING_PATTERNS
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def detect(self, responses: list[AgentResponse]) -> ConflictReport:
        """执行冲突检测。

        Args:
            responses: specialist 响应列表（可能为空）。

        Returns:
            :class:`ConflictReport`：冲突报告（无冲突时 ``has_conflict=False``）。

        Notes:
            - 空输入 → 返回 ``has_conflict=False`` 的占位报告；
            - 单条响应 → 仅做「自检」（不与他人比较），confidence_gap 不
              适用但仍可触发 recommendation_conflict 兜底。
        """
        if not responses:
            return ConflictReport(
                has_conflict=False,
                conflict_types=[],
                conflicting_responses=[],
                resolution_suggestion=ConflictResolution.KEEP_HIGHEST_CONFIDENCE,
                detail="no responses to check",
            )

        conflict_types: list[ConflictType] = []
        conflicting: list[AgentResponse] = []
        details: list[str] = []

        # 1) GRADING_CONFLICT：同器官不同分级
        grading_conflicts = self._detect_grading_conflicts(responses)
        if grading_conflicts:
            conflict_types.append(ConflictType.GRADING_CONFLICT)
            for organ, resp_pair in grading_conflicts:
                conflicting.extend(resp_pair)
            details.append(
                f"grading conflict across {len(grading_conflicts)} organ(s): "
                + ", ".join(
                    f"{organ}={sorted({_extract_grade(r, organ, self._grading_extractors) or '?' for r in resp_pair})}"
                    for organ, resp_pair in grading_conflicts
                )
            )

        # 2) CONFIDENCE_CONFLICT：confidence 差距 > 阈值
        confidence_conflict = self._detect_confidence_conflict(responses)
        if confidence_conflict:
            conflict_types.append(ConflictType.CONFIDENCE_CONFLICT)
            conflicting.extend(confidence_conflict)
            details.append(
                f"confidence gap > {self._confidence_gap_threshold}: "
                f"max={max(r.confidence for r in responses):.4f}, "
                f"min={min(r.confidence for r in responses):.4f}"
            )

        # 3) RECOMMENDATION_CONFLICT：处置建议互斥
        recommendation_conflict = self._detect_recommendation_conflict(responses)
        if recommendation_conflict:
            conflict_types.append(ConflictType.RECOMMENDATION_CONFLICT)
            conflicting.extend(recommendation_conflict)
            details.append(
                "recommendation conflict: mutually exclusive suggestion groups detected"
            )

        # 4) EVIDENCE_CONFLICT：证据互斥
        evidence_conflict = self._detect_evidence_conflict(responses)
        if evidence_conflict:
            conflict_types.append(ConflictType.EVIDENCE_CONFLICT)
            conflicting.extend(evidence_conflict)
            details.append(
                "evidence conflict: contradicting claims detected across responses"
            )

        has_conflict = bool(conflict_types)
        if not has_conflict:
            return ConflictReport(
                has_conflict=False,
                conflict_types=[],
                conflicting_responses=[],
                resolution_suggestion=ConflictResolution.KEEP_HIGHEST_CONFIDENCE,
                detail="no conflict detected",
            )

        # 解决建议：confidence / grading → KEEP_HIGHEST_CONFIDENCE；
        # recommendation / evidence → REQUIRE_HUMAN_REVIEW（语义无法自动消解）
        if ConflictType.RECOMMENDATION_CONFLICT in conflict_types or ConflictType.EVIDENCE_CONFLICT in conflict_types:
            resolution = ConflictResolution.REQUIRE_HUMAN_REVIEW
        elif len(conflict_types) == 1 and conflict_types[0] == ConflictType.CONFIDENCE_CONFLICT:
            # 纯置信度差距：取最高 conf 即可
            resolution = ConflictResolution.KEEP_HIGHEST_CONFIDENCE
        elif len(conflict_types) == 1 and conflict_types[0] == ConflictType.GRADING_CONFLICT:
            # 同器官分级冲突：取最高 conf（保守策略）
            resolution = ConflictResolution.KEEP_HIGHEST_CONFIDENCE
        else:
            # 多类冲突：人工复核
            resolution = ConflictResolution.REQUIRE_HUMAN_REVIEW

        # 去重 conflicting 列表（按 agent_name 保留首次出现）
        seen: set[str] = set()
        unique_conflicting: list[AgentResponse] = []
        for r in conflicting:
            if r.agent_name not in seen:
                seen.add(r.agent_name)
                unique_conflicting.append(r)

        return ConflictReport(
            has_conflict=True,
            conflict_types=conflict_types,
            conflicting_responses=unique_conflicting,
            resolution_suggestion=resolution,
            detail="; ".join(details),
        )

    # ------------------------------------------------------------------
    # 内部：4 类冲突的具体检测
    # ------------------------------------------------------------------

    def _detect_grading_conflicts(
        self,
        responses: list[AgentResponse],
    ) -> list[tuple[str, list[AgentResponse]]]:
        """检测同器官内的分级冲突。

        逻辑：按 organ 分组 → 对同器官的多条响应抽取分级码 → 出现 ≥2 种不同
        分级码即视为该器官有分级冲突。

        Returns:
            ``[(organ_value, [conflicting_responses, ...]), ...]`` 列表。
        """
        # 1) 按 organ 分组（None organ 归入 "__none__" 不参与器官内冲突）
        by_organ: dict[str, list[AgentResponse]] = defaultdict(list)
        for r in responses:
            organ_key = r.organ.value if r.organ is not None else "__none__"
            by_organ[organ_key].append(r)

        conflicts: list[tuple[str, list[AgentResponse]]] = []
        for organ_value, group in by_organ.items():
            if organ_value == "__none__" or len(group) < 2:
                # 单条响应 / 缺失 organ → 无法形成器官内冲突
                continue
            # 抽取每条响应的分级码（用 list[(resp, grade)] 避免 AgentResponse
            # 作为 dict key 的可哈希性问题）
            grade_by_resp: list[tuple[AgentResponse, str | None]] = [
                (r, _extract_grade(r, organ_value, self._grading_extractors)) for r in group
            ]
            distinct_grades: set[str] = {
                g for _, g in grade_by_resp if g is not None
            }
            # 至少有 2 条响应能抽出分级码 + 至少 2 种不同的分级码
            extractable_count = sum(1 for _, g in grade_by_resp if g is not None)
            if extractable_count >= 2 and len(distinct_grades) >= 2:
                conflicts.append((organ_value, group))
        return conflicts

    def _detect_confidence_conflict(
        self,
        responses: list[AgentResponse],
    ) -> list[AgentResponse]:
        """检测置信度冲突。

        逻辑：max - min > 阈值 → 把 max 和 min 对应的响应都列为冲突响应。
        仅在响应数 ≥ 2 时生效。
        """
        if len(responses) < 2:
            return []
        confidences = [r.confidence for r in responses]
        gap = max(confidences) - min(confidences)
        if gap <= self._confidence_gap_threshold:
            return []
        max_resp = max(responses, key=lambda r: r.confidence)
        min_resp = min(responses, key=lambda r: r.confidence)
        # 去重：若 max == min（极端情况）只返回一次
        if max_resp.agent_name == min_resp.agent_name:
            return [max_resp]
        return [max_resp, min_resp]

    def _detect_recommendation_conflict(
        self,
        responses: list[AgentResponse],
    ) -> list[AgentResponse]:
        """检测处置建议冲突。

        逻辑：扫描每条响应的 ``content``，按 ``_RECOMMENDATION_GROUPS`` 分类
        → 同一器官内出现 ≥2 个互斥组的关键词即视为冲突。
        """
        if len(responses) < 2:
            return []
        by_organ: dict[str, list[AgentResponse]] = defaultdict(list)
        for r in responses:
            organ_key = r.organ.value if r.organ is not None else "__none__"
            by_organ[organ_key].append(r)

        conflict_responses: list[AgentResponse] = []
        for organ_value, group in by_organ.items():
            if len(group) < 2:
                continue
            groups_present: set[int] = set()
            for r in group:
                content_lower = (r.content or "").lower()
                for idx, tokens in enumerate(self._recommendation_groups):
                    if any(tok.lower() in content_lower for tok in tokens):
                        groups_present.add(idx)
            if len(groups_present) >= 2:
                conflict_responses.extend(group)
        return conflict_responses

    def _detect_evidence_conflict(
        self,
        responses: list[AgentResponse],
    ) -> list[AgentResponse]:
        """检测证据冲突（P0-3 修复：从 no-op 升级为真实检测）。

        触发条件（满足任一即触发 EVIDENCE_CONFLICT）：

        1. **共享证据 + 互斥建议**：≥2 条响应共享至少 1 个 evidence_chunk_id，
           但所给的处置建议属于不同 recommendation group（活检 vs 随访 / 紧急）；
        2. **共享证据 + 同器官分级冲突**：≥2 条响应共享 evidence_chunk_id 且
           属于同一器官，给出的分级码不同（与 GRADING_CONFLICT 互补）；
        3. **共享证据 + 否定关系冲突**：≥2 条响应共享 evidence_chunk_id 且
           对同一被否定对象（"X 不是 Y"）出现自相矛盾（如 specialist A 说
           "排除恶性"而 specialist B 说 "排除良性"）。

        Returns:
            参与证据冲突的响应列表（去重）。

        Notes:
            - **非共享证据的冲突不由 EVIDENCE_CONFLICT 兜底**：分属不同
              互斥组的建议由 RECOMMENDATION_CONFLICT 捕获；同器官分级冲
              突由 GRADING_CONFLICT 捕获。本方法专注于"引用同一 chunk 但
              结论相反"的语义级证据矛盾。
            - **保守策略**：仅在确有共享 evidence_chunk_id 时才触发，避免
              误报（与原 no-op 实现的过度保守形成对照 — 现在保守的是"误
              报率低"，而不是"全部漏报"）。
        """
        if len(responses) < 2:
            return []

        conflict_responses: list[AgentResponse] = []
        seen: set[str] = set()  # 去重 by agent_name

        # 对每对 (i, j) 响应进行配对检查
        for i, ri in enumerate(responses):
            for rj in responses[i + 1 :]:
                evidence_i = set(ri.evidence_chunk_ids or [])
                evidence_j = set(rj.evidence_chunk_ids or [])
                shared = evidence_i & evidence_j
                if not shared:
                    continue  # 无共享证据 → 不属于 EVIDENCE_CONFLICT

                # 条件 1: 共享证据 + 互斥建议
                groups_i = _recommendation_groups_in(ri, self._recommendation_groups)
                groups_j = _recommendation_groups_in(rj, self._recommendation_groups)
                if groups_i and groups_j and groups_i.isdisjoint(groups_j):
                    _add_unique(conflict_responses, seen, ri)
                    _add_unique(conflict_responses, seen, rj)
                    logger.info(
                        "evidence_conflict_shared_evidence_contradicting_recommendations",
                        shared_chunks=sorted(shared),
                        resp_i=ri.agent_name,
                        resp_j=rj.agent_name,
                    )
                    continue

                # 条件 2: 共享证据 + 同器官分级冲突
                organ_i = ri.organ.value if ri.organ else None
                organ_j = rj.organ.value if rj.organ else None
                if organ_i and organ_i == organ_j and organ_i in self._grading_extractors:
                    grade_i = _extract_grade(ri, organ_i, self._grading_extractors)
                    grade_j = _extract_grade(rj, organ_j, self._grading_extractors)
                    if (
                        grade_i is not None
                        and grade_j is not None
                        and grade_i != grade_j
                    ):
                        _add_unique(conflict_responses, seen, ri)
                        _add_unique(conflict_responses, seen, rj)
                        logger.info(
                            "evidence_conflict_shared_evidence_contradicting_grades",
                            shared_chunks=sorted(shared),
                            organ=organ_i,
                            grades=[grade_i, grade_j],
                        )
                        continue

                # 条件 3: 共享证据 + 否定关系冲突（同一被否定对象互斥）
                negated_i = _extract_negated_objects(ri.content or "")
                negated_j = _extract_negated_objects(rj.content or "")
                common_negated = negated_i & negated_j
                # 同一对象都被否定 → 反而是"一致排除"，不算冲突；
                # 真正的冲突是"否定对象完全不同" → 说明双方基于同一证据
                # 得出截然不同的诊断排除。
                if negated_i and negated_j and not common_negated and not negated_i.isdisjoint(
                    {"恶性", "malignant", "良性", "benign"}
                ):
                    _add_unique(conflict_responses, seen, ri)
                    _add_unique(conflict_responses, seen, rj)
                    logger.info(
                        "evidence_conflict_shared_evidence_contradicting_negations",
                        shared_chunks=sorted(shared),
                        negated_i=sorted(negated_i),
                        negated_j=sorted(negated_j),
                    )

        return conflict_responses


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def _extract_grade(
    response: AgentResponse,
    organ_value: str,
    extractors: dict[str, tuple[re.Pattern, str]],
) -> str | None:
    """从响应的 ``content`` 抽取器官级分级码。

    Args:
        response: specialist 响应。
        organ_value: organ 字符串（``"thyroid"`` / ``"liver"`` / ``"cardiac"``）。
        extractors: 分级抽取正则表。

    Returns:
        分级码字符串（如 ``"TR3"`` / ``"LR-5"`` / ``"LVEF40"``），抽取失败
        返回 ``None``。
    """
    if organ_value not in extractors:
        return None
    pattern, fmt = extractors[organ_value]
    text = response.content or ""
    try:
        m = pattern.search(text)
    except Exception as exc:
        # 正则抽取异常 → 返回 None 是降级，但**失败要可见**：
        # 否则「这条结论里确实没写分级」与「抽取器坏了」分不出来（A.62 批 2-B2）。
        logger.debug("grade_pattern_search_failed", error=str(exc))
        return None
    if m is None:
        return None
    # 找到第一个非空 group（多模式正则中只有一个 group 会有值）
    captured = None
    for g in m.groups():
        if g is not None:
            captured = g
            break
    if captured is None:
        return None
    try:
        return fmt.format(captured)
    except Exception as exc:
        logger.debug("grade_format_failed", error=str(exc))
        return None


def _recommendation_groups_in(
    response: AgentResponse,
    recommendation_groups: list[tuple[str, ...]],
) -> set[int]:
    """返回 response.content 命中的 recommendation group 索引集合。"""
    content_lower = (response.content or "").lower()
    groups: set[int] = set()
    for idx, tokens in enumerate(recommendation_groups):
        if any(tok.lower() in content_lower for tok in tokens):
            groups.add(idx)
    return groups


def _extract_negated_objects(content: str) -> set[str]:
    """抽取 content 中被否定的对象 token 集合（小写化）。

    用途：P0-3 EVIDENCE_CONFLICT 条件 3 — 检测"排除 X" vs "排除 Y"
    的自相矛盾。
    """
    negated: set[str] = set()
    for pattern in _NEGATION_PATTERNS:
        try:
            for m in pattern.finditer(content):
                token = m.group(1).strip().lower()
                if 1 <= len(token) <= 20:
                    negated.add(token)
        except Exception as exc:
            logger.debug("negation_extract_failed", error=str(exc))
            continue
    return negated


def _add_unique(
    acc: list[AgentResponse],
    seen: set[str],
    response: AgentResponse,
) -> None:
    """去重添加到列表（按 agent_name）。"""
    if response.agent_name not in seen:
        seen.add(response.agent_name)
        acc.append(response)


__all__ = [
    "DEFAULT_CONFIDENCE_GAP_THRESHOLD",
    "DEFAULT_GRADING_PATTERNS",
    "ConflictDetector",
]


# 导出别名（用于测试和外部依赖显式 import）
DEFAULT_GRADING_PATTERNS = _GRADING_PATTERNS
