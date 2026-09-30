"""T-GA-FUS-02 — Self-RAG 自我评估模块。

本模块实现 Self-RAG 的核心评估器 (:class:`SelfRAGEvaluator`)。

Self-RAG 流程：

1. 接收待评估的 :class:`AgentResponse`；
2. 构造 self-evaluation prompt（评估"诊断是否有充分证据" / "证据是否
   与诊断结论一致" / "是否有遗漏的关键特征"）；
3. 调用 LLM 自我评估；
4. 解析输出 → :class:`SelfRAGEvalResult`（pass / retry / needs_review）；
5. 验收：质检通过率 ≥ 80%。

评分维度（5 分制）：

- **证据充分性**（0-5）：诊断是否有充分证据支持（Y/N + 0-5 分）；
- **证据一致性**（0-5）：证据与诊断结论是否一致；
- **关键特征**（0-5）：是否有遗漏的关键特征（5 = 无遗漏）。

总分 = min(三个维度)。``total >= pass_threshold`` → passed=True。

设计要点：

- **失败安全**：LLM 调用失败 → 返回 ``passed=False, score=0.0,
  reason="self_rag_eval_failed"``，由编排器决定后续（CRAG 兜底）；
- **启发式兜底**：当未配置 LLM 引擎（``inference_engine=None``）时，
  使用基于响应本身的启发式评估（confidence >= heuristic_threshold 即视为
  通过），保留测试可达性。
- **可注入**：评分阈值、LLM 引擎均通过构造参数注入。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from app.agents.protocols import AgentResponse
from app.inference.protocols import InferenceEngine, InferenceRequest

logger = structlog.get_logger("app.fusion.self_rag")


# ----------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------


@dataclass
class SelfRAGEvalResult:
    """Self-RAG 评估结果。

    Attributes:
        passed: 是否通过质检。
        score: 总分（0.0 - 5.0，0 = 完全失败，5 = 完美）。
        evidence_sufficiency: 证据充分性（0-5）。
        evidence_consistency: 证据一致性（0-5）。
        feature_completeness: 关键特征完整性（0-5）。
        missing_features: 检出的遗漏特征列表（LLM 输出）。
        reason: 失败原因（仅 passed=False 时有意义）。
    """

    passed: bool
    score: float
    evidence_sufficiency: float = 0.0
    evidence_consistency: float = 0.0
    feature_completeness: float = 0.0
    missing_features: list[str] = None  # type: ignore[assignment]
    reason: str = ""

    def __post_init__(self) -> None:
        if self.missing_features is None:
            self.missing_features = []


# ----------------------------------------------------------------------
# SelfRAGEvaluator
# ----------------------------------------------------------------------


#: 评分通过阈值（总分 >= 阈值 → passed=True；推荐 3.0）
DEFAULT_PASS_THRESHOLD = 3.0

#: 启发式兜底阈值（当 inference_engine=None 时使用；confidence >= 阈值 → passed）
DEFAULT_HEURISTIC_THRESHOLD = 0.7

#: 单维度评分上限
MAX_SCORE = 5.0

#: 最大重试次数（2 = 最多 3 次生成机会；包含首次生成）
#: P1-1 修复：实现 Self-RAG 重试机制（设计文档 02-核心流程与知识库.md §2.1）
DEFAULT_MAX_RETRIES = 2

#: 重试间隔（秒）— 防止 LLM 短时过载
DEFAULT_RETRY_DELAY = 0.0

#: self-evaluation prompt 模板
_SELF_RAG_PROMPT = """你是一名严谨的超声诊断质检员。请对以下诊断结论做自我评估，按 3 个维度各打 0-5 分。

# 诊断结论
{diagnosis}

# 引用的知识
{evidence}

# 评估维度
1. 证据充分性（0-5）：诊断是否有充分证据支持？0=完全无证据，5=证据非常充分
2. 证据一致性（0-5）：证据与诊断结论是否一致？0=完全矛盾，5=完全一致
3. 关键特征（0-5）：是否有遗漏的关键特征？5=无遗漏，0=大量遗漏

# 输出格式（严格遵守）
```
evidence_sufficiency: <0-5>
evidence_consistency: <0-5>
feature_completeness: <0-5>
missing_features: <逗号分隔的特征列表，如 "无" 或 "钙化, 边界">
```
"""


class SelfRAGEvaluator:
    """Self-RAG 评估器（T-GA-FUS-02）。

    Args:
        inference_engine: LLM 推理引擎（用于 self-evaluation 调用）。
            ``None`` 时降级为基于响应 confidence 的启发式评估。
        pass_threshold: 总分 >= 阈值即视为通过（默认 3.0）。
        heuristic_threshold: 启发式兜底阈值（仅在 ``inference_engine=None``
            时使用；confidence >= 阈值即 passed=True）。
        max_tokens: LLM 生成上限（默认 256）。
        max_retries: 最大重试次数（P1-1，默认 2 = 3 次生成机会）。
        retry_delay: 重试间隔秒数（默认 0）。
    """

    def __init__(
        self,
        inference_engine: InferenceEngine | None = None,
        pass_threshold: float = DEFAULT_PASS_THRESHOLD,
        heuristic_threshold: float = DEFAULT_HEURISTIC_THRESHOLD,
        max_tokens: int = 256,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> None:
        if not (0.0 <= pass_threshold <= MAX_SCORE):
            raise ValueError(
                f"pass_threshold must be in [0.0, {MAX_SCORE}], got {pass_threshold}"
            )
        if not (0.0 <= heuristic_threshold <= 1.0):
            raise ValueError(
                f"heuristic_threshold must be in [0.0, 1.0], got {heuristic_threshold}"
            )
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if retry_delay < 0.0:
            raise ValueError("retry_delay must be >= 0.0")
        self._inference = inference_engine
        self._pass_threshold = pass_threshold
        self._heuristic_threshold = heuristic_threshold
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_delay = retry_delay

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def evaluate(self, response: AgentResponse) -> SelfRAGEvalResult:
        """执行 Self-RAG 评估（P1-1 修复：实现最多 max_retries 次重试）。

        流程：
        1) 启发式兜底：未配置 LLM → 单次启发式评估（不计重试）；
        2) LLM 自我评估：循环尝试最多 ``max_retries+1`` 次：
           - 单次成功（pass=True）→ 立即返回；
           - 失败 → 若还有重试机会则重试（间隔 ``retry_delay`` 秒）；
           - 全部失败 → 返回最后一次的评估结果，``reason`` 包含
             ``retries_exhausted`` 标记，便于 CRAG 识别。
        3) 失败安全：LLM 调用异常 → 单次回退到启发式评估（不重试 LLM）。

        Args:
            response: 待评估的 specialist 响应。

        Returns:
            :class:`SelfRAGEvalResult`：评估结果（passed / score / 维度分）。

        Notes:
            CRAG 补充检索不计入 Self-RAG 重试次数（设计文档 02-核心流程 §2.1）：
            CRAG 是 Self-RAG 全部失败后的兜底链路，与重试是正交关系。
        """
        # 1) 启发式兜底：未配置 LLM → 单次启发式评估（不计重试）
        if self._inference is None:
            return self._heuristic_evaluate(response)

        # 2) LLM 自我评估（带重试）
        prompt = self._build_prompt(response)
        last_result: SelfRAGEvalResult | None = None
        max_attempts = self._max_retries + 1  # 包含首次生成
        for attempt in range(1, max_attempts + 1):
            try:
                request = InferenceRequest(
                    prompt=prompt,
                    max_tokens=self._max_tokens,
                    request_id=f"self_rag_{response.agent_name}_attempt{attempt}",
                )
                llm_response = await self._inference.generate(request)
                text = llm_response.text or ""
            except Exception as exc:
                logger.warning(
                    "self_rag_llm_failed_falling_back_to_heuristic",
                    agent=response.agent_name,
                    attempt=attempt,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                # LLM 调用异常 → 单次回退启发式，不重试 LLM
                return self._heuristic_evaluate(
                    response, reason=f"llm_failed: {type(exc).__name__}"
                )

            # 解析 LLM 输出
            parsed = _parse_self_rag_output(text)
            if parsed is None:
                logger.warning(
                    "self_rag_parse_failed",
                    agent=response.agent_name,
                    attempt=attempt,
                    llm_text_preview=text[:100],
                )
                # 解析失败 → 启发式兜底（不重试 LLM；多次解析同样格式很可能同样失败）
                return self._heuristic_evaluate(response, reason="llm_parse_failed")

            # 评分
            evidence_sufficiency, evidence_consistency, feature_completeness, missing = parsed
            total = min(evidence_sufficiency, evidence_consistency, feature_completeness)
            passed = total >= self._pass_threshold
            reason = "" if passed else f"score={total:.2f} < {self._pass_threshold}"
            if attempt > 1:
                reason = f"retry#{attempt}: {reason}" if reason else f"retry#{attempt}: passed"
            last_result = SelfRAGEvalResult(
                passed=passed,
                score=round(total, 2),
                evidence_sufficiency=evidence_sufficiency,
                evidence_consistency=evidence_consistency,
                feature_completeness=feature_completeness,
                missing_features=missing,
                reason=reason,
            )

            if passed:
                if attempt > 1:
                    logger.info(
                        "self_rag_recovered_after_retry",
                        agent=response.agent_name,
                        attempt=attempt,
                        score=total,
                    )
                return last_result

            # 未通过：是否还有重试机会？
            # T-Perf 修正（2026-10-01 真机）：确定性零分（三维度全 0，典型场景
            # =KB 无该器官知识、证据不足）重发同样输入不会得到不同结果——真机
            # 实测 cardiac 例 3 次重试全部 score=0.00，白耗 2 次 LLM 调用
            # （~1.2s/例）。零分直接返回；重试机会留给"接近阈值的采样波动"
            # （0 < total < threshold）。
            if total == 0:
                last_result.reason = f"zero_score_no_retry: {reason}"
                return last_result
            if attempt < max_attempts:
                logger.info(
                    "self_rag_retry_scheduled",
                    agent=response.agent_name,
                    attempt=attempt,
                    score=total,
                    reason=reason,
                    next_attempt=attempt + 1,
                )
                if self._retry_delay > 0:
                    import asyncio
                    await asyncio.sleep(self._retry_delay)

        # 全部重试失败 → 返回最后一次结果 + 标记 retries_exhausted
        assert last_result is not None  # 循环至少执行 1 次
        last_result.reason = (
            f"retries_exhausted ({max_attempts} attempts): {last_result.reason}"
        )
        logger.warning(
            "self_rag_retries_exhausted",
            agent=response.agent_name,
            attempts=max_attempts,
            final_score=last_result.score,
        )
        return last_result

    # ------------------------------------------------------------------
    # 内部：prompt / 启发式 / 解析
    # ------------------------------------------------------------------

    def _build_prompt(self, response: AgentResponse) -> str:
        """构造 self-evaluation prompt。"""
        evidence_str = (
            f"agent={response.agent_name}, "
            f"organ={response.organ.value if response.organ else 'unknown'}, "
            f"confidence={response.confidence:.4f}, "
            f"evidence_chunks={len(response.evidence_chunk_ids)}"
        )
        return _SELF_RAG_PROMPT.format(
            diagnosis=response.content or "(empty)",
            evidence=evidence_str,
        )

    def _heuristic_evaluate(
        self,
        response: AgentResponse,
        reason: str = "",
    ) -> SelfRAGEvalResult:
        """启发式评估（无 LLM 时使用，或 LLM 调用 / 解析失败时回退）。

        评分规则：
        - evidence_sufficiency = min(len(evidence) / 5, 1.0) * 5
        - evidence_consistency = response.confidence * 5
        - feature_completeness = (1.0 if response.content else 0.0) * 5
        - total = min(三个维度)

        注意：``reason`` 参数总是被保留（即使 passed=True），便于调用方
        区分"启发式主动判定通过"和"LLM 失败后兜底通过"。
        """
        evidence_count = len(response.evidence_chunk_ids or [])
        evidence_sufficiency = min(evidence_count / 5.0, 1.0) * MAX_SCORE
        evidence_consistency = float(response.confidence) * MAX_SCORE
        feature_completeness = (MAX_SCORE if response.content else 0.0)
        total = min(evidence_sufficiency, evidence_consistency, feature_completeness)
        passed = (
            response.confidence >= self._heuristic_threshold
            and evidence_count > 0
            and bool(response.content)
        )
        # 总是保留 reason（包括 passed=True 时的 "llm_failed_fallback" 等）
        final_reason = reason or ("" if passed else "heuristic_below_threshold")
        return SelfRAGEvalResult(
            passed=passed,
            score=round(total, 2),
            evidence_sufficiency=round(evidence_sufficiency, 2),
            evidence_consistency=round(evidence_consistency, 2),
            feature_completeness=round(feature_completeness, 2),
            missing_features=[],
            reason=final_reason,
        )


# ----------------------------------------------------------------------
# 输出解析
# ----------------------------------------------------------------------

_FIELD_PATTERNS = {
    "evidence_sufficiency": re.compile(
        r"evidence_sufficiency\s*[:：]\s*([0-5](?:\.\d+)?)", re.IGNORECASE
    ),
    "evidence_consistency": re.compile(
        r"evidence_consistency\s*[:：]\s*([0-5](?:\.\d+)?)", re.IGNORECASE
    ),
    "feature_completeness": re.compile(
        r"feature_completeness\s*[:：]\s*([0-5](?:\.\d+)?)", re.IGNORECASE
    ),
    "missing_features": re.compile(
        r"missing_features\s*[:：]\s*([^\n]+)", re.IGNORECASE
    ),
}


def _parse_self_rag_output(
    text: str,
) -> tuple[float, float, float, list[str]] | None:
    """解析 self-rag LLM 输出。

    期望格式（每行一个字段）：
    ```
    evidence_sufficiency: 4
    evidence_consistency: 5
    feature_completeness: 3
    missing_features: 钙化, 边界
    ```

    Returns:
        ``(evidence_sufficiency, evidence_consistency, feature_completeness, missing)``。
        解析失败 → 返回 ``None``。
    """
    if not text or not text.strip():
        return None
    try:
        sufficiency_m = _FIELD_PATTERNS["evidence_sufficiency"].search(text)
        consistency_m = _FIELD_PATTERNS["evidence_consistency"].search(text)
        completeness_m = _FIELD_PATTERNS["feature_completeness"].search(text)
        missing_m = _FIELD_PATTERNS["missing_features"].search(text)
        if not (sufficiency_m and consistency_m and completeness_m):
            return None
        sufficiency = float(sufficiency_m.group(1))
        consistency = float(consistency_m.group(1))
        completeness = float(completeness_m.group(1))
        # clamp to [0, 5]
        sufficiency = max(0.0, min(MAX_SCORE, sufficiency))
        consistency = max(0.0, min(MAX_SCORE, consistency))
        completeness = max(0.0, min(MAX_SCORE, completeness))
        missing_text = (missing_m.group(1).strip() if missing_m else "")
        missing_list: list[str] = []
        if missing_text and missing_text.lower() not in {"无", "none", "n/a", ""}:
            missing_list = [
                t.strip() for t in re.split(r"[,，;；\n]", missing_text) if t.strip()
            ]
        return sufficiency, consistency, completeness, missing_list
    except Exception:
        return None


__all__ = [
    "DEFAULT_HEURISTIC_THRESHOLD",
    "DEFAULT_PASS_THRESHOLD",
    "SelfRAGEvalResult",
    "SelfRAGEvaluator",
]
