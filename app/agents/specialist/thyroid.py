"""T-GA-AGT-04 — 甲状腺专科智能体（TI-RADS 5 等级分级）。

``ThyroidSpecialistAgent`` 继承 :class:`RAGSpecialistAgent`，按 ACR TI-RADS 2017
标准对甲状腺结节做 5 等级分级（TR1-TR5）。

行为：
1. ``invoke()`` 公共流程：RAG 检索 → prompt 组装 → LLM 调用；
2. ``_parse_response()`` 从 LLM 输出中抽取 TR1-TR5 分级码；
3. 置信度 = 启发式基线 + 等级抽取清晰度加成（明确 "TRx" 模式 +0.1）；
4. 降级：RAG 空 / LLM 失败 / 分级码缺失 → confidence=0.0 占位响应。
"""
from __future__ import annotations

import re

import structlog

from app.agents.protocols import AgentResponse
from app.agents.specialist.base import RAGSpecialistAgent, normalize_grading_text
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ

logger = structlog.get_logger("app.agents.specialist.thyroid")


# TI-RADS 5 等级定义
TI_RADS_LEVELS = ("TR1", "TR2", "TR3", "TR4", "TR5")
TI_RADS_DEFAULT = "TR3"  # 解析失败时的兜底（最保守的中等风险等级）

# TI-RADS 分级抽取正则（支持 "TR4" / "TR-4" / "tr 4" / "TIRADS 4" 等变体）
_TI_RADS_PATTERN = re.compile(
    r"\bT[\s\-_]?I[\s\-_]?R[\s\-_]?A[\s\-_]?D[\s\-_]?S?\s*[\-:]?\s*([1-5])\b",
    re.IGNORECASE,
)
_TI_RADS_SIMPLE_PATTERN = re.compile(
    r"\bTR\s*[\-:]?\s*([1-5])\b",
    re.IGNORECASE,
)
# 抽取「总分」用于交叉验证（可选）
_TI_RADS_SCORE_PATTERN = re.compile(
    r"(?:总分|score)\s*[:：=]?\s*(\d{1,2})",
    re.IGNORECASE,
)


class ThyroidSpecialistAgent(RAGSpecialistAgent):
    """甲状腺专科智能体（TI-RADS 5 等级分级，T-GA-AGT-04）。

    Args:
        inference_engine: 底层 LLM 推理引擎。
        knowledge_agent: 知识查询智能体（含 RAG + 两层缓存）。
        prompt_template: 可选自定义 prompt。``None`` 时从
            ``app/agents/prompts/thyroid.md`` 加载。
        rag_top_k: RAG 检索返回 chunk 上限。默认 5。
        max_tokens: LLM 生成上限。默认 1024。
    """

    organ: Organ = Organ.THYROID

    def get_organ(self) -> Organ:
        return Organ.THYROID

    def get_grading_system(self) -> str:
        return "TI-RADS"

    def _parse_response(
        self,
        llm_text: str,
        chunks: list[KnowledgeChunk],
    ) -> AgentResponse:
        """从 LLM 输出中抽取 TR1-TR5 分级码。

        抽取规则（按优先级）：
        1. 完整 ``TI-RADS`` / ``TIRADS`` 关键字（最高优先）；
        2. ``TR1`` - ``TR5`` 简化形式；
        3. 若 LLM 输出多个分级码，取**数字最大**（最严重）；
        4. 抽取失败 → 返回 ``confidence=0.0`` 占位响应。

        置信度计算：
        - 启发式基线（来自 :meth:`_compute_heuristic_confidence`）；
        - 明确分级码格式加成 ``+0.1``（"TR4" 形式） / ``+0.15``（"TI-RADS 4" 形式）；
        - 总分匹配加成 ``+0.05``（LLM 输出包含"总分"且数值与等级阈值一致）；
        - 封顶 1.0。
        """
        normalized = normalize_grading_text(llm_text)
        grade: str | None = None
        score_bonus = 0.0

        # 1) 优先匹配完整 TI-RADS 关键字
        match = _TI_RADS_PATTERN.search(normalized)
        if match:
            grade = f"TR{match.group(1)}"
            score_bonus = 0.15
        else:
            # 2) 简化 TR 模式
            match = _TI_RADS_SIMPLE_PATTERN.search(normalized)
            if match:
                grade = f"TR{match.group(1)}"
                score_bonus = 0.1

        # 3) 多个分级码取最严重
        all_matches = _TI_RADS_SIMPLE_PATTERN.findall(normalized)
        if len(all_matches) > 1:
            max_num = max(int(m) for m in all_matches)
            grade = f"TR{max_num}"

        # 抽不到分级码 → 降级
        if grade is None or grade not in TI_RADS_LEVELS:
            logger.warning(
                "thyroid_grade_not_detected",
                llm_text_preview=normalized[:200],
            )
            return AgentResponse(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content=(
                    f"[thyroid] grade_unparseable: LLM output did not contain a "
                    f"valid TI-RADS grade (one of the five canonical levels); "
                    f"manual review required. LLM preview: {normalized[:200]}"
                ),
                confidence=0.0,
                evidence_chunk_ids=[c.chunk_id for c in chunks[:5]],
                reasoning_trace=[
                    "grading_system -> TI-RADS",
                    "grade_unparseable: no TR1-TR5 match",
                ],
            )

        # 4) 总分匹配加成（如果 LLM 同时输出了"总分"）
        score_match = _TI_RADS_SCORE_PATTERN.search(normalized)
        if score_match:
            score = int(score_match.group(1))
            expected_min = {"TR1": 0, "TR2": 2, "TR3": 3, "TR4": 4, "TR5": 7}[grade]
            expected_max = {"TR1": 0, "TR2": 2, "TR3": 3, "TR4": 6, "TR5": 14}[grade]
            if expected_min <= score <= expected_max:
                score_bonus += 0.05

        # 5) 置信度组装
        base_conf = self._compute_heuristic_confidence(chunks, llm_text)
        final_conf = round(min(base_conf + score_bonus, 1.0), 4)

        # 6) 提取理由（取"理由"或"分级"行后一段）
        reason = self._extract_reason(normalized, grade)

        # 7) 选 top-3 证据 chunk
        evidence_ids = [c.chunk_id for c in chunks[:3]]

        return AgentResponse(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content=(
                f"[thyroid] {grade} — {reason}"
                if reason else f"[thyroid] {grade}"
            ),
            confidence=final_conf,
            evidence_chunk_ids=evidence_ids,
            reasoning_trace=[
                "grading_system -> TI-RADS",
                f"grade -> {grade}",
                f"score_bonus -> {score_bonus}",
            ],
        )

    @staticmethod
    def _extract_reason(normalized_text: str, grade: str) -> str:
        """从 LLM 输出中抽取「理由」行。

        启发式：找包含 "理由" / "因为" / "由于" 关键字的行，取其后续内容；
        找不到则返回空字符串。
        """
        for line in normalized_text.split("\n"):
            line_s = line.strip()
            if not line_s:
                continue
            if any(kw in line_s for kw in ("理由", "因为", "由于", "Reason", "reason")):
                # 去掉前缀（"理由:" / "理由："）
                for prefix in ("理由:", "理由：", "Reason:", "Reason：", "因为", "由于"):
                    if line_s.startswith(prefix):
                        return line_s[len(prefix):].strip()
                return line_s
        return ""


__all__ = [
    "TI_RADS_LEVELS",
    "ThyroidSpecialistAgent",
]
