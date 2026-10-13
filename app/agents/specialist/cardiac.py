"""T-GA-AGT-06 — 心脏专科智能体（LVEF 4 档分级）。

``CardiacSpecialistAgent`` 继承 :class:`RAGSpecialistAgent`，按 ASE 2015 标准
对左心室收缩功能做 LVEF 评估（normal / mildly / moderately / severely reduced）。

行为：
1. ``invoke()`` 公共流程：RAG 检索 → prompt 组装 → LLM 调用；
2. ``_parse_response()`` 从 LLM 输出中抽取 LVEF 百分比和严重程度；
3. 置信度 = 启发式基线 + 数值清晰度加成；
4. 降级：RAG 空 / LLM 失败 / 数值缺失 → confidence=0.0 占位响应。
"""
from __future__ import annotations

import re

import structlog

from app.agents.protocols import AgentResponse
from app.agents.specialist.base import (
    RAGSpecialistAgent,
    normalize_grading_text,
    preview_digest,
)
from app.inference.grading_definitions import extract_grading_severest
from app.inference.grading_schema import extract_guided_grading
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ

logger = structlog.get_logger("app.agents.specialist.cardiac")


# ASE 2015 LVEF 4 档分级
LVEF_SEVERITY_LEVELS = (
    "normal",
    "mildly_reduced",
    "moderately_reduced",
    "severely_reduced",
)

# canonical 严重度（`_GRADE_HIERARCHY["cardiac"]`）→ 本专科现网标签。
# 两套是同一个语义的两组写法：hierarchy 用 short 名（评测/一致性口径），
# 本专科沿用报告口径的 `*_reduced`。统一映射只此一处，避免再分叉。
_CANONICAL_TO_CARDIAC_LABEL: dict[str, str] = {
    "normal": "normal",
    "mild": "mildly_reduced",
    "moderate": "moderately_reduced",
    "severe": "severely_reduced",
}

# LVEF 百分比 → 严重程度（基于 ASE 2015）
def _lvef_to_severity(lvef: float) -> str:
    """根据 LVEF 数值返回严重程度分级。"""
    if lvef >= 55.0:
        return "normal"
    if lvef >= 45.0:
        return "mildly_reduced"
    if lvef >= 30.0:
        return "moderately_reduced"
    return "severely_reduced"


# LVEF 数值抽取正则（支持 "EF 50%" / "LVEF 50%" / "射血分数 50%" 等）
_LVEF_NUMBER_PATTERN = re.compile(
    r"(?:LVEF|EF|射血分数|EF值)\s*[:：=]?\s*(\d{1,3}(?:\.\d+)?)\s*%?",
    re.IGNORECASE,
)
# 严重程度关键字（备选抽取路径）
_LVEF_SEVERITY_PATTERNS = [
    (re.compile(r"normal|正常", re.IGNORECASE), "normal"),
    (re.compile(r"mildly[\s_]?reduced|轻度降低|轻度减低", re.IGNORECASE), "mildly_reduced"),
    (re.compile(r"moderately[\s_]?reduced|中度降低|中度减低", re.IGNORECASE), "moderately_reduced"),
    (re.compile(r"severely[\s_]?reduced|重度降低|重度减低", re.IGNORECASE), "severely_reduced"),
]


class CardiacSpecialistAgent(RAGSpecialistAgent):
    """心脏专科智能体（LVEF 评估，T-GA-AGT-06）。

    Args:
        inference_engine: 底层 LLM 推理引擎。
        knowledge_agent: 知识查询智能体（含 RAG + 两层缓存）。
        prompt_template: 可选自定义 prompt。``None`` 时从
            ``app/agents/prompts/cardiac.md`` 加载。
        rag_top_k: RAG 检索返回 chunk 上限。默认 5。
        max_tokens: LLM 生成上限。默认 1024。
    """

    organ: Organ = Organ.CARDIAC

    def get_organ(self) -> Organ:
        return Organ.CARDIAC

    def get_grading_system(self) -> str:
        return "LVEF"

    def _grade_from_structured(self, text: str) -> str | None:
        """结构化重试输出 → canonical 严重度（``{"grading": "severe"}`` → ``severe``）。"""
        return extract_guided_grading(text or "", "cardiac")

    def _parse_response(
        self,
        llm_text: str,
        chunks: list[KnowledgeChunk],
        *,
        grade_override: str | None = None,
    ) -> AgentResponse:
        """从 LLM 输出中抽取 LVEF 百分比和严重程度。

        抽取规则（按优先级）：
        1. LVEF 数值（如 "50%" / "EF 50"）→ 推算严重程度；
        2. 直接关键字（"mildly_reduced" / "轻度降低"）；
        3. 多个数值时取**最低**（最保守）；
        4. 抽取失败 → confidence=0.0 占位响应。

        置信度计算：
        - 启发式基线 + 数值清晰度加成（明确 "50%" 形式 +0.1）；
        - 严重程度 + 数值**一致**加成（+0.1）（说明 LLM 内部逻辑自洽）；
        - 封顶 1.0。

        **R6 修正（矛盾检测）**：当 LLM 同时输出"EF 数值"和"严重程度
        关键字"，但二者推算结果**不一致**时（如 "EF 60%, severely_reduced"，
        数值推算 normal，关键字 severely_reduced），在 ``reasoning_trace``
        追加 ``severity_mismatch: ...`` 标签，并将 ``confidence`` 扣减
        0.1（保守策略 — 与原 spec 描述一致）。**不**自动改写 severity —
        保留 LLM 原始输出供 FUS 轨道做最终决策。
        """
        normalized = normalize_grading_text(llm_text)
        lvef_value: float | None = None
        severity: str | None = None
        trace_extra: list[str] = []  # 矛盾检测标签（追加到 reasoning_trace）

        # 0) 体系分级（单一事实源，2026-10-13 分级提取根治）：
        #    采样层结构化输出（约束解码重试的 `{"grading": "moderate"}`）/ canonical
        #    关键词（"正常"/"轻度降低"/"mild"）—— 与评测、双引擎一致性校验同一套
        #    `_GRADING_PATTERNS`。数值路径（1) 仍保留：LVEF 具体数值是心脏报告的核心
        #    信息，且 R6 矛盾检测依赖它做交叉验证。
        if grade_override:
            # 结构化重试已给出分级（base.invoke 步骤 5b）：直接映射，不再猜
            severity = _CANONICAL_TO_CARDIAC_LABEL.get(grade_override)
        else:
            guided = extract_guided_grading(normalized, "cardiac")
            canonical = guided or extract_grading_severest(normalized, "cardiac")
            if canonical is not None:
                severity = _CANONICAL_TO_CARDIAC_LABEL.get(canonical)

        # 1) 数值抽取
        for match in _LVEF_NUMBER_PATTERN.finditer(normalized):
            try:
                val = float(match.group(1))
                if 0.0 <= val <= 100.0:
                    if lvef_value is None:
                        lvef_value = val
                    else:
                        # 多个数值取最低（保守）
                        lvef_value = min(lvef_value, val)
            except (ValueError, TypeError):
                continue

        # 2) 关键字抽取（备选）
        if severity is None:
            for pattern, label in _LVEF_SEVERITY_PATTERNS:
                if pattern.search(normalized):
                    severity = label
                    break

        # 3) 数值 → 严重程度推算
        computed_severity: str | None = None
        if lvef_value is not None:
            computed_severity = _lvef_to_severity(lvef_value)
            if severity is None:
                severity = computed_severity

        # 3.5) **R6 矛盾检测**：数值推算 vs 关键字不一致
        severity_mismatch = False
        if (
            lvef_value is not None
            and severity is not None
            and computed_severity is not None
            and severity != computed_severity
        ):
            severity_mismatch = True
            trace_extra.append(
                f"severity_mismatch: lvef={lvef_value:g}% -> "
                f"computed={computed_severity} but keyword={severity}"
            )

        # 4) 抽取失败 → **可读降级**（2026-10-13 分级提取根治；技术细节只进日志/trace）
        if severity is None:
            logger.warning(
                "cardiac_lvef_not_detected",
                organ="cardiac",
                llm_raw_len=len(normalized),
                llm_preview_sha=preview_digest(normalized),
            )
            return AgentResponse(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content=(
                    "本例未能解析出左心室收缩功能分级（AI 输出未给出可判定的 LVEF 值"
                    "或严重程度），已转人工判读：请结合图像与病史自行评估。"
                ),
                confidence=0.0,
                evidence_chunk_ids=[c.chunk_id for c in chunks[:5]],
                reasoning_trace=[
                    "grading_system -> LVEF",
                    "grading_unparseable: no LVEF grade extracted",
                    f"llm_raw_len: {len(normalized)}",
                    f"llm_preview_sha: {preview_digest(normalized)}",
                ],
                needs_review=True,
            )

        logger.info(
            "cardiac_grade_extracted",
            organ="cardiac",
            severity=severity,
            lvef_value=lvef_value,
            llm_raw_len=len(normalized),
        )

        # 5) 置信度组装
        base_conf = self._compute_heuristic_confidence(chunks, llm_text)
        score_bonus = 0.0
        if lvef_value is not None:
            score_bonus += 0.1
            # 一致性加成：数值推算的严重程度 == 关键字抽取的严重程度
            if severity == _lvef_to_severity(lvef_value):
                score_bonus += 0.1
        # R6: 矛盾检测扣减（保守策略 — spec 描述）
        if severity_mismatch:
            score_bonus -= 0.1

        final_conf = round(min(base_conf + score_bonus, 1.0), 4)

        # 6) 抽取理由
        reason = self._extract_reason(normalized)

        # 7) 选 top-3 证据
        evidence_ids = [c.chunk_id for c in chunks[:3]]

        # 8) 组装 content
        lvef_str = f"{lvef_value:g}%" if lvef_value is not None else "unknown"
        if reason:
            content = f"[cardiac] LVEF={lvef_str}, severity={severity} — {reason}"
        else:
            content = f"[cardiac] LVEF={lvef_str}, severity={severity}"

        reasoning_trace = [
            "grading_system -> LVEF",
            f"lvef_value -> {lvef_value}",
            f"severity -> {severity}",
            f"score_bonus -> {score_bonus}",
        ]
        # 追加矛盾检测标签（在器官级 trace 末尾）
        reasoning_trace.extend(trace_extra)

        # R6: 矛盾检测额外设置 needs_review = True（更激进的安全策略）
        needs_review = severity_mismatch

        return AgentResponse(
            agent_name="cardiac_specialist",
            organ=Organ.CARDIAC,
            content=content,
            confidence=final_conf,
            evidence_chunk_ids=evidence_ids,
            reasoning_trace=reasoning_trace,
            needs_review=needs_review,
        )

    @staticmethod
    def _extract_reason(normalized_text: str) -> str:
        """从 LLM 输出中抽取「理由」行。"""
        for line in normalized_text.split("\n"):
            line_s = line.strip()
            if not line_s:
                continue
            if any(kw in line_s for kw in ("理由", "因为", "由于", "Reason", "reason")):
                for prefix in ("理由:", "理由：", "Reason:", "Reason：", "因为", "由于"):
                    if line_s.startswith(prefix):
                        return line_s[len(prefix):].strip()
                return line_s
        return ""


__all__ = [
    "LVEF_SEVERITY_LEVELS",
    "CardiacSpecialistAgent",
    "_lvef_to_severity",
]
