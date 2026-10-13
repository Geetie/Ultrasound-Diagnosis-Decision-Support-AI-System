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
from app.agents.specialist.base import (
    RAGSpecialistAgent,
    normalize_grading_text,
    preview_digest,
)
from app.agents.specialist.readable import graded_content
from app.inference.grading_definitions import (
    extract_grading_severest,
    grade_major_level,
)
from app.inference.grading_schema import extract_guided_grading
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


def _to_tr_canonical(raw: str | None) -> str | None:
    """分级码 → 专科 canonical ``TR1..TR5``（**4a/4b/4c 收敛为主级 TR4**）。

    细分（4a/4b/4c）只保留在 ``reasoning_trace`` 里供审计；下游报告 / 融合 /
    评测一律按 ``TR1..TR5`` 消费（口径与映射源见 16 号 A.56）。未知码返回 ``None``
    —— 交给调用方走兜底，不猜等级。
    """
    major = grade_major_level("thyroid", raw)
    return f"TR{major}" if major is not None else None


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

    def _grade_from_structured(self, text: str) -> str | None:
        """结构化重试输出 → canonical 分级（``{"grading": "4b"}`` → ``TR4``）。"""
        return _to_tr_canonical(extract_guided_grading(text or "", "thyroid"))

    def _parse_response(
        self,
        llm_text: str,
        chunks: list[KnowledgeChunk],
        *,
        grade_override: str | None = None,
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

        # 抽取优先级（2026-10-13 分级提取根治；单一事实源 = app/inference/grading_definitions）：
        #   1) 采样层结构化输出（约束解码重试的 `{"grading": "4b"}`）
        #   2) canonical 数字形式（"TI-RADS 4a" / "分级: 4a" / "4 类" / "TR4a"）
        #      —— 与评测集、双引擎一致性校验**同一套** `_GRADING_PATTERNS`；原先专科
        #      自己一套平行正则，才出现"评测抽得到、生产结论抽不到"的 `grade_unparseable`
        #   3) 器官正则兜底（"TR4" 简化形式，保留现网语义）
        #   4) 都抽不到 → 降级（见下方占位响应）
        # 多码取重由 `extract_grading_severest` 统一保证（原实现的「多码取 max」收敛到这里）。
        if grade_override:
            # 结构化重试已给出分级（base.invoke 步骤 5b）：直接采用，不再猜；
            # 富文本正文仍来自首次输出 —— 重试路径是「合并」而不是「覆盖」。
            grade = grade_override
            score_bonus = 0.15
        else:
            guided = extract_guided_grading(normalized, "thyroid")
            raw = guided or extract_grading_severest(normalized, "thyroid")
            grade = _to_tr_canonical(raw)
            if grade is not None:
                # 结构化 / canonical 都属「明确的体系分级」，按完整形式给加成
                score_bonus = 0.15
            else:
                match = _TI_RADS_SIMPLE_PATTERN.search(normalized) or _TI_RADS_PATTERN.search(normalized)
                if match:
                    grade = f"TR{match.group(1)}"
                    score_bonus = 0.1

        # 抽不到分级码 → **可读降级**（2026-10-13 分级提取根治）
        # 医生可见正文必须是可读中文；模型原文的技术细节只进日志与 reasoning_trace
        # （且只留长度与摘要，不落原文 —— 模型输出可能含 PHI）。
        if grade is None or grade not in TI_RADS_LEVELS:
            logger.warning(
                "thyroid_grade_not_detected",
                organ="thyroid",
                llm_raw_len=len(normalized),
                llm_preview_sha=preview_digest(normalized),
            )
            return AgentResponse(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content=(
                    "本例未能解析出 TI-RADS 分级（AI 输出未给出可判定的分级码），"
                    "已转人工判读：请结合图像与病史自行分级。"
                ),
                confidence=0.0,
                evidence_chunk_ids=[c.chunk_id for c in chunks[:5]],
                reasoning_trace=[
                    "grading_system -> TI-RADS",
                    "grading_unparseable: no TI-RADS grade extracted",
                    f"llm_raw_len: {len(normalized)}",
                    f"llm_preview_sha: {preview_digest(normalized)}",
                ],
                needs_review=True,
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

        logger.info(
            "thyroid_grade_extracted",
            organ="thyroid",
            grade=grade,
            score_bonus=score_bonus,
            llm_raw_len=len(normalized),
        )

        # 6) 提取理由（取"理由"或"分级"行后一段）
        reason = self._extract_reason(normalized, grade)

        # 7) 选 top-3 证据 chunk
        evidence_ids = [c.chunk_id for c in chunks[:3]]

        return AgentResponse(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            # A.59 可读化：原先写 `[thyroid] TR3 — …`（英文器官代码进医生可见正文）
            content=graded_content(Organ.THYROID, grade, reason),
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
