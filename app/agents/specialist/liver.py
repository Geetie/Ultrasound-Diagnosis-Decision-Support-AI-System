"""T-GA-AGT-05 — 肝脏专科智能体（LI-RADS 分级）。

``LiverSpecialistAgent`` 继承 :class:`RAGSpecialistAgent`，按 LI-RADS v2017
简化算法对肝脏局灶性病变做分级（LR-1 ~ LR-5 / LR-M / LR-TIV）。

行为：
1. ``invoke()`` 公共流程：RAG 检索 → prompt 组装 → LLM 调用；
2. ``_parse_response()`` 从 LLM 输出中抽取 LR-* 分级码；
3. 置信度 = 启发式基线 + 风险等级加权（LR-5 > LR-TIV > LR-M > LR-4 > LR-3 > LR-2 > LR-1）；
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

logger = structlog.get_logger("app.agents.specialist.liver")


# LI-RADS 等级定义 + 风险权重（数值越大风险越高）
LI_RADS_LEVELS = ("LR-1", "LR-2", "LR-3", "LR-4", "LR-5", "LR-M", "LR-TIV")
# 风险排序：LR-5 > LR-TIV > LR-M > LR-4 > LR-3 > LR-2 > LR-1
# 依据：LR-5 是「明确 HCC」（100% 恶性） — 风险最高；
# LR-TIV 是「明确肿瘤侵犯血管」 — 严重但诊断确定性次之。
LI_RADS_RISK_WEIGHT = {
    "LR-1": 0.05,   # 良性 — 置信度加成极低
    "LR-2": 0.10,
    "LR-3": 0.30,
    "LR-4": 0.50,
    "LR-5": 0.95,   # 明确 HCC — 风险最高
    "LR-M": 0.65,   # 倾向恶性但可能不是 HCC
    "LR-TIV": 0.85, # 明确肿瘤侵犯血管
}

# LI-RADS 分级抽取正则
_LI_RADS_PATTERN = re.compile(
    r"\bLR\s*[\-_:：]?\s*([1-5M]|TIV)\b",
    re.IGNORECASE,
)


def _to_lr_canonical(raw: str | None) -> str | None:
    """分级码 → 专科 canonical ``LR-1..LR-5``（单一事实源 ``_GRADE_HIERARCHY``）。

    ``LR-M`` / ``LR-TIV`` **不在** ``_GRADE_HIERARCHY``（没有「主级」语义，它们是
    独立类别）→ 返回 ``None``，由调用方的器官正则兜底承接，行为与改造前一致。
    """
    major = grade_major_level("liver", raw)
    return f"LR-{major}" if major is not None else None


class LiverSpecialistAgent(RAGSpecialistAgent):
    """肝脏专科智能体（LI-RADS 分级，T-GA-AGT-05）。

    Args:
        inference_engine: 底层 LLM 推理引擎。
        knowledge_agent: 知识查询智能体（含 RAG + 两层缓存）。
        prompt_template: 可选自定义 prompt。``None`` 时从
            ``app/agents/prompts/liver.md`` 加载。
        rag_top_k: RAG 检索返回 chunk 上限。默认 5。
        max_tokens: LLM 生成上限。默认 1024。
    """

    organ: Organ = Organ.LIVER

    def get_organ(self) -> Organ:
        return Organ.LIVER

    def get_grading_system(self) -> str:
        return "LI-RADS"

    def _grade_from_structured(self, text: str) -> str | None:
        """结构化重试输出 → canonical 分级（``{"grading": "3"}`` → ``LR-3``）。"""
        return _to_lr_canonical(extract_guided_grading(text or "", "liver"))

    def _parse_response(
        self,
        llm_text: str,
        chunks: list[KnowledgeChunk],
        *,
        grade_override: str | None = None,
    ) -> AgentResponse:
        """从 LLM 输出中抽取 LR-1 ~ LR-5 / LR-M / LR-TIV 分级码。

        抽取规则：
        1. 匹配 ``LR-1`` ~ ``LR-5`` / ``LR-M`` / ``LR-TIV``（含空格 / 横线变体）；
        2. 多个分级码时按风险权重取**最高**；
        3. 抽取失败 → 返回 ``confidence=0.0`` 占位响应。

        置信度计算：
        - 启发式基线 + 风险权重加成（不超过 0.9，避免 LLM 误判高等级时
          confidence 虚高）；
        - 封顶 0.95。
        """
        normalized = normalize_grading_text(llm_text)
        grade: str | None = None

        # 抽取优先级（与 thyroid 同范式，2026-10-13 分级提取根治；单一事实源
        # = app/inference/grading_definitions）：
        #   1) 采样层结构化输出（约束解码重试的 `{"grading": "3"}`）
        #   2) canonical 数字形式（"LI-RADS 3" / "分级: 3" / "LR3"）
        #   3) 器官正则兜底：**LR-M / LR-TIV 只在此路径**（它们无主级语义，
        #      由 `LI_RADS_RISK_WEIGHT` 取风险最高，保留现网行为）
        if grade_override:
            # 结构化重试已给出分级（base.invoke 步骤 5b）：直接采用，不再猜
            grade = grade_override
        else:
            guided = extract_guided_grading(normalized, "liver")
            grade = _to_lr_canonical(guided or extract_grading_severest(normalized, "liver"))

        if grade is None:
            # 1) 单个匹配
            match = _LI_RADS_PATTERN.search(normalized)
            if match:
                suffix = match.group(1).upper()
                if suffix == "TIV":
                    grade = "LR-TIV"
                else:
                    grade = f"LR-{suffix}"

            # 2) 多个匹配时取风险最高
            all_matches = _LI_RADS_PATTERN.findall(normalized)
            if len(all_matches) > 1:
                candidates = []
                for m in all_matches:
                    suffix = m.upper()
                    if suffix == "TIV":
                        candidates.append("LR-TIV")
                    else:
                        candidates.append(f"LR-{suffix}")
                grade = max(candidates, key=lambda g: LI_RADS_RISK_WEIGHT.get(g, 0))

        # 抽不到 → **可读降级**（2026-10-13 分级提取根治；技术细节只进日志/trace）
        if grade is None or grade not in LI_RADS_LEVELS:
            logger.warning(
                "liver_grade_not_detected",
                organ="liver",
                llm_raw_len=len(normalized),
                llm_preview_sha=preview_digest(normalized),
            )
            return AgentResponse(
                agent_name="liver_specialist",
                organ=Organ.LIVER,
                content=(
                    "本例未能解析出 LI-RADS 分级（AI 输出未给出可判定的分级码），"
                    "已转人工判读：请结合图像与病史自行分级。"
                ),
                confidence=0.0,
                evidence_chunk_ids=[c.chunk_id for c in chunks[:5]],
                reasoning_trace=[
                    "grading_system -> LI-RADS",
                    "grading_unparseable: no LI-RADS grade extracted",
                    f"llm_raw_len: {len(normalized)}",
                    f"llm_preview_sha: {preview_digest(normalized)}",
                ],
                needs_review=True,
            )

        logger.info(
            "liver_grade_extracted",
            organ="liver",
            grade=grade,
            llm_raw_len=len(normalized),
        )

        # 3) 置信度组装：启发式 + 风险权重
        base_conf = self._compute_heuristic_confidence(chunks, llm_text)
        risk_boost = LI_RADS_RISK_WEIGHT.get(grade, 0.3)
        # 偏向「对高风险结论保守」：高风险加成不超过 0.9
        final_conf = round(min(base_conf * 0.5 + risk_boost * 0.5, 0.95), 4)

        # 4) 抽取理由
        reason = self._extract_reason(normalized)

        # 5) 选 top-3 证据
        evidence_ids = [c.chunk_id for c in chunks[:3]]

        return AgentResponse(
            agent_name="liver_specialist",
            organ=Organ.LIVER,
            # A.59 可读化：原先写 `[liver] LR-5 — …`（英文器官代码进医生可见正文）
            content=graded_content(Organ.LIVER, grade, reason),
            confidence=final_conf,
            evidence_chunk_ids=evidence_ids,
            reasoning_trace=[
                "grading_system -> LI-RADS",
                f"grade -> {grade}",
                f"risk_boost -> {risk_boost}",
            ],
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
    "LI_RADS_LEVELS",
    "LI_RADS_RISK_WEIGHT",
    "LiverSpecialistAgent",
]
