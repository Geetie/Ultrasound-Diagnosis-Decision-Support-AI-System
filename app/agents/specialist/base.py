"""T-GA-AGT-04/05/06 — 专科智能体抽象基类与公共实现。

本模块提供器官专科智能体的两层抽象：

1. ``SpecialistAgent`` — 极薄标记类（仅约束 ``organ`` 属性）。
   ``PlaceholderSpecialistAgent`` 继承自本类，不依赖 RAG/LLM。

2. ``RAGSpecialistAgent`` — 继承 ``SpecialistAgent``，注入
   ``InferenceEngine`` + ``KnowledgeAgent``，实现 ``invoke()`` 公共流程：

   ```
   AgentContext → knowledge.query(top_k=5) → build_prompt → inference.generate
                                                                       │
                                          ┌────────────────────────────┘
                                          ▼
                                    _parse_response → AgentResponse
                                                                       │
                                          ┌────────────────────────────┘
                                          ▼
                              used_evidence_ids 写入 + 阈值检查 → 返回
   ```

   真实专科（ThyroidSpecialistAgent / LiverSpecialistAgent / CardiacSpecialistAgent）
   继承自 ``RAGSpecialistAgent``，只需实现器官级别的「分级解析」逻辑
   （``_parse_response``、``get_organ``、``get_grading_system``）。

设计要点：
- 公共 invoke 流程统一处理：RAG 检索、prompt 组装、LLM 调用、置信度计算。
- 置信度计算：``min(RAG top score, 1.0) * 0.6 + LLM 完整度 0.4``（启发式）。
  依据：现有 KnowledgeAgent 抽象不暴露 score，因此走 RAG chunk 计数
  + LLM 输出的非空比例作为替代信号。**LLM 完整度饱和阈值 = 500 字符**（与
  实际 few-shot 输出长度对齐，避免 200 字符过早饱和导致评分粒度丢失）。
- 降级路径：RAG 返回空、LLM 抛错、解析失败 → 全部返回
  ``confidence=0.0`` 的占位响应（不抛异常），与 ``Agent`` 协议契约一致。
- P99 预算：RAG 1s + LLM 3s + 调度 1s = 5s。
- **D2 修正**：invoke 末尾将 ``response.evidence_chunk_ids`` 写入
  ``context.used_evidence_ids``（支持 KB safety_critical 更新的精确回溯）。
- **D1 修正**：若构造时注入 ``organ_threshold``（器官级置信度阈值），
  invoke 末尾检查 final_conf，低于阈值时设置 ``response.needs_review=True``
  并在 trace 中追加 ``below_organ_threshold: ...`` 标签。
"""
from __future__ import annotations

import inspect
import re
import time
from abc import abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.agents.protocols import Agent, AgentContext, AgentResponse
from app.agents.protocols import KnowledgeAgent as KnowledgeAgentProtocol
from app.inference.protocols import InferenceEngine, InferenceRequest
from app.knowledge.clinical_actions import actions_for
from app.knowledge.differential import (
    differential_summary,
    parse_differential_diagnoses,
)
from app.knowledge.measurement_extract import (
    measurement_features,
    structured_measurement_from_text,
)
from app.knowledge.protocols import KnowledgeChunk
from app.knowledge.risk_rules import (
    compute_malignancy_risk,
    extract_udaf_features,
    pick_posterior_acoustic_term,
    ti_rads_points_for_posterior,
)
from app.knowledge.vocabulary import posterior_acoustic_value
from app.models.schemas import Organ, PosteriorAcoustic

logger = structlog.get_logger("app.agents.specialist.base")

# Prompt 模板目录（与 app/agents/prompts/*.md 对齐）
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# RAG 检索默认 top_k（与提示词 Few-shot 中"5 条"对齐）
DEFAULT_RAG_TOP_K = 5


def _accepts_kwarg(func: Callable[..., Any], name: str) -> bool:
    """探测 ``func`` 是否接受名为 ``name`` 的关键字参数。

    T-GB-AGT-02 需要向 ``KnowledgeAgent.query`` 传入 ``profile``，但历史
    实现（测试替身、第三方注入）只声明 ``query(query, organ, top_k)``。
    这里做一次性能力探测：显式声明该参数、或带 ``**kwargs``（含
    ``MagicMock``）均视为支持。探测失败（无法取签名）时保守返回 False。
    """
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )

# 置信度计算权重（RAG 0.6 + LLM 0.4）
_RAG_CONFIDENCE_WEIGHT = 0.6
_LLM_CONFIDENCE_WEIGHT = 0.4

# LLM 因子饱和阈值（500 字符达到 1.0 — 与 few-shot 输出实际长度对齐）
_LLM_TEXT_SATURATION_CHARS = 500

# 空证据阈值：top_k 全部为空 → confidence=0.0 并标记 "evidence_insufficient"
EMPTY_RAG_RESULT_CONFIDENCE = 0.0


# ----------------------------------------------------------------------
# 抽象基类（极薄 — 仅约束 organ 属性）
# ----------------------------------------------------------------------


class SpecialistAgent(Agent):
    """器官专科智能体抽象基类（极薄标记类）。

    子类必须声明 ``organ: Organ`` 类属性。本基类**不**做任何实现，
    仅为 ``Agent`` ABC 之上的器官级标记，便于：
    - 类型提示区分专科 vs. dispatcher/monitor/knowledge；
    - 运行时反射（如 dispatcher 启动期按 organ 路由到对应实例）。

    若需 RAG + LLM 公共流程，请继承 :class:`RAGSpecialistAgent`。
    """

    organ: Organ  # 子类必须声明（类属性）


# ----------------------------------------------------------------------
# RAG 启用的专科智能体抽象类（公共 invoke 流程）
# ----------------------------------------------------------------------


class RAGSpecialistAgent(SpecialistAgent):
    """启用 RAG + LLM 的专科智能体抽象基类。

    提供 ``invoke()`` 公共流程；子类只需实现：
    - :meth:`get_organ` — 返回本专科对应的 Organ 枚举值。
    - :meth:`get_grading_system` — 返回分级体系名称（"TI-RADS" /
      "LI-RADS" / "LVEF"），用于响应 trace 和监控埋点。
    - :meth:`_parse_response` — 把 LLM 输出 + RAG 证据解析为
      :class:`AgentResponse`（器官级别分级逻辑）。

    Args:
        inference_engine: 底层 LLM 推理引擎。
        knowledge_agent: 知识查询智能体（含 RAG + 两层缓存）。
        prompt_template: 可选的自定义 system prompt 文本。``None`` 时
            自动从 ``app/agents/prompts/{organ.value}.md`` 加载。
        rag_top_k: RAG 检索返回 chunk 上限。默认 5。
        max_tokens: LLM 生成上限。默认 1024。
        organ_threshold: 器官级置信度阈值。**D1 修正**（设计 §2.4）：
            低于本阈值的 final_conf 会触发 ``response.needs_review = True``
            并在 trace 中追加 ``below_organ_threshold`` 标签。``None``
            时不触发阈值检查（保持向后兼容）。**典型值**：thyroid 0.85 /
            liver 0.88 / cardiac 0.92。
        disease_thresholds: 病种级置信度阈值映射（``{disease_type: threshold}``）。
            **D1 修正**（设计 §2.4）：子类可在 _parse_response 末尾按
            检出的病种调用 :meth:`_check_disease_threshold` 进一步收紧
            阈值。``None`` 或空字典时不做病种级检查。
    """

    def __init__(
        self,
        inference_engine: InferenceEngine,
        knowledge_agent: KnowledgeAgentProtocol,
        prompt_template: Optional[str] = None,
        rag_top_k: int = DEFAULT_RAG_TOP_K,
        max_tokens: int = 1024,
        organ_threshold: Optional[float] = None,
        disease_thresholds: Optional[Dict[str, float]] = None,
    ) -> None:
        if inference_engine is None:
            raise ValueError("inference_engine must not be None")
        if knowledge_agent is None:
            raise ValueError("knowledge_agent must not be None")
        if rag_top_k <= 0:
            raise ValueError("rag_top_k must be positive")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if organ_threshold is not None and not (0.0 <= organ_threshold <= 1.0):
            raise ValueError(
                f"organ_threshold must be in [0.0, 1.0] if set, got {organ_threshold}"
            )
        if disease_thresholds is not None:
            for dtype, th in disease_thresholds.items():
                if not (0.0 <= th <= 1.0):
                    raise ValueError(
                        f"disease_thresholds[{dtype!r}] must be in [0.0, 1.0], got {th}"
                    )

        self._inference = inference_engine
        self._knowledge = knowledge_agent
        self._rag_top_k = rag_top_k
        self._max_tokens = max_tokens
        # T-GB-AGT-02：探测知识智能体是否支持 ``profile`` 关键字参数。
        # 生产实现（KnowledgeAgentImpl）支持；历史测试替身只声明
        # ``query(query, organ, top_k)``，此时不透传档位以保持兼容。
        self._knowledge_accepts_profile = _accepts_kwarg(
            knowledge_agent.query, "profile"
        )
        # D1: 器官级/病种级阈值
        self._organ_threshold = organ_threshold
        self._disease_thresholds: Dict[str, float] = dict(disease_thresholds or {})
        # 注意：用 ``is None`` 而非 ``or`` 判定，避免空字符串被吞掉
        # 当作「无自定义 prompt」回退到默认模板（保留对空字符串的拒绝校验）。
        if prompt_template is None:
            self._prompt_template = self._load_default_prompt()
        else:
            self._prompt_template = prompt_template

        # 启动期检查 prompt 非空（避免空 prompt 打到 LLM）
        if not self._prompt_template or not self._prompt_template.strip():
            raise ValueError(
                f"prompt template is empty for {self.__class__.__name__}; "
                "either pass a non-empty prompt_template or ensure the .md file exists"
            )

    # ------------------------------------------------------------------
    # 抽象方法（子类必须实现）
    # ------------------------------------------------------------------

    @abstractmethod
    def get_organ(self) -> Organ:
        """返回本专科对应的 Organ 枚举值。

        Returns:
            ``Organ.THYROID`` / ``Organ.LIVER`` / ``Organ.CARDIAC`` 之一。
        """
        raise NotImplementedError

    @abstractmethod
    def get_grading_system(self) -> str:
        """返回分级体系名称（如 "TI-RADS" / "LI-RADS" / "LVEF"）。

        用于：
        - 响应 ``reasoning_trace`` 中标注分级体系；
        - 监控埋点（按 organ + grading_system 维度）。
        """
        raise NotImplementedError

    @abstractmethod
    def _parse_response(
        self,
        llm_text: str,
        chunks: List[KnowledgeChunk],
    ) -> AgentResponse:
        """把 LLM 输出 + RAG 证据解析为 AgentResponse。

        器官级实现要点：
        - 从 LLM 输出中抽取分级（如 "TR4" / "LR-5" / "LVEF 40%"）；
        - 计算器官级置信度（在父类启发式基础上叠加器官级信号）；
        - 选择 top-N 证据 chunk id 加入 ``evidence_chunk_ids``。

        Args:
            llm_text: LLM 生成的原始文本。
            chunks: RAG 检索返回的 chunk 列表（按相关性降序）。

        Returns:
            ``AgentResponse``：必须设置 ``agent_name`` / ``organ`` /
            ``content`` / ``confidence`` / ``evidence_chunk_ids`` /
            ``reasoning_trace``。
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 公共 invoke 流程
    # ------------------------------------------------------------------

    async def invoke(self, context: AgentContext) -> AgentResponse:
        """公共 invoke 流程：RAG → prompt → LLM → parse → 后处理。

        行为契约：
        1) 调用 :meth:`_retrieve_chunks` 拿 top-k 知识 chunk（异常 → 降级
           为空列表，不抛）；
        2) chunks 为空 → 返回 ``confidence=0.0`` 的占位响应
           （evidence_insufficient），标记需要人工复核；
        3) 否则调用 :meth:`_build_prompt` 拼装完整 prompt；
        4) 调用 :meth:`_call_llm` 拿 LLM 输出（异常 → 降级为 confidence=0.0）；
        5) 调用 :meth:`_parse_response` 完成器官级解析；
        6) **D2 修正**：将 ``response.evidence_chunk_ids`` ``extend`` 到
           ``context.used_evidence_ids``（KB safety_critical 精确取消用）；
        7) **D1 修正**：若设置了 ``organ_threshold``，低于阈值时
           设置 ``response.needs_review=True`` 并追加 trace 标签。

        Returns:
            :class:`AgentResponse`：含器官级分级结果 / 置信度 / 证据 / trace。
        """
        start = time.monotonic()
        organ = self.get_organ()
        trace: List[str] = [
            f"route -> {organ.value}",
            f"grading_system -> {self.get_grading_system()}",
        ]

        # 1) RAG 检索（异常时不抛，降级为空列表）
        try:
            chunks = await self._retrieve_chunks(context, organ)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "specialist_rag_failed",
                organ=organ.value,
                request_id=context.request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            chunks = []
            trace.append(f"rag_failed: {type(exc).__name__}")

        # 2) 空证据 → 降级
        if not chunks:
            trace.append("evidence_insufficient: zero chunks returned by RAG")
            response = AgentResponse(
                agent_name=self._agent_name(),
                organ=organ,
                content=(
                    f"[{organ.value}] evidence_insufficient: "
                    "no relevant knowledge retrieved; manual review required"
                ),
                confidence=EMPTY_RAG_RESULT_CONFIDENCE,
                evidence_chunk_ids=[],
                reasoning_trace=trace,
                latency_ms=(time.monotonic() - start) * 1000.0,
            )
            self._post_process(context, response)
            return response

        trace.append(f"rag_chunks: {len(chunks)}")

        # 3) 拼装 prompt
        prompt = self._build_prompt(context, chunks, trace)

        # 4) LLM 调用（异常时不抛，降级为 confidence=0.0）
        try:
            llm_text = await self._call_llm(context, prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "specialist_llm_failed",
                organ=organ.value,
                request_id=context.request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            trace.append(f"llm_failed: {type(exc).__name__}")
            response = AgentResponse(
                agent_name=self._agent_name(),
                organ=organ,
                content=(
                    f"[{organ.value}] llm_unavailable: "
                    f"{type(exc).__name__}; manual review required"
                ),
                confidence=0.0,
                evidence_chunk_ids=[c.chunk_id for c in chunks],
                reasoning_trace=trace,
                latency_ms=(time.monotonic() - start) * 1000.0,
            )
            self._post_process(context, response)
            return response

        trace.append(f"llm_text_len: {len(llm_text)}")

        # 5) 器官级解析
        try:
            response = self._parse_response(llm_text, chunks)
        except Exception as exc:  # noqa: BLE001
            # 解析失败 → 降级（不要让 specialist 抛错阻塞 dispatcher）
            logger.warning(
                "specialist_parse_failed",
                organ=organ.value,
                request_id=context.request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            trace.append(f"parse_failed: {type(exc).__name__}")
            response = AgentResponse(
                agent_name=self._agent_name(),
                organ=organ,
                content=(
                    f"[{organ.value}] parse_failed: "
                    f"{type(exc).__name__}; manual review required"
                ),
                confidence=0.0,
                evidence_chunk_ids=[c.chunk_id for c in chunks],
                reasoning_trace=trace,
                latency_ms=(time.monotonic() - start) * 1000.0,
            )
            self._post_process(context, response)
            return response

        # 5.5) M3/M5/M9：结构化测量 + 后方回声 + 恶性概率（增强能力，失败不影响主流程）
        self._enrich_clinical_attributes(response, llm_text, chunks)

        # 合并 trace + 顶层 latency（保留器官级 trace 顺序）
        if response.reasoning_trace:
            # 器官级 trace 在前，公共 trace 在后
            response.reasoning_trace = list(response.reasoning_trace) + trace
        else:
            response.reasoning_trace = trace
        response.organ = organ  # 防御性：确保 organ 被设置
        response.agent_name = self._agent_name()
        response.latency_ms = (time.monotonic() - start) * 1000.0

        # 6) 公共后处理（D1 阈值检查 + D2 used_evidence_ids 写入）
        self._post_process(context, response)
        return response

    # ------------------------------------------------------------------
    # Agent 协议默认实现（子类可覆盖）
    # ------------------------------------------------------------------

    def get_capability_tags(self) -> List[str]:
        organ = self.get_organ()
        return [
            organ.value,
            "specialist",
            f"grading_{self.get_grading_system().lower()}",
        ]

    async def health_check(self) -> bool:
        """健康检查：LLM + RAG 任意一个可用即视为健康（graceful degradation）。

        任何一边失败都吞掉，只 log warning — 与 SpecialistAgent 的
        降级语义一致（即使 RAG 失败也能给出"evidence_insufficient"响应，
        不应直接报 unhealthy 让 dispatcher 把整个 specialist 拉黑）。
        """
        try:
            rag_ok = await self._knowledge.health_check()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "specialist_health_rag_error",
                organ=self.get_organ().value,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            rag_ok = False
        try:
            llm_ok = await self._inference.health_check()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "specialist_health_llm_error",
                organ=self.get_organ().value,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            llm_ok = False
        # 至少一个可用即视为健康（与 invoke 降级语义一致）
        return rag_ok or llm_ok

    async def close(self) -> None:
        """关闭 specialist（不关闭共享的 inference / knowledge，交给 lifespan）。

        子类持有的状态（如 prompt 缓存）由本方法释放。
        """
        self._prompt_template = ""
        logger.info("specialist_closed", organ=self.get_organ().value)

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _agent_name(self) -> str:
        """生成 agent_name（``{organ}_specialist``）。"""
        return f"{self.get_organ().value}_specialist"

    async def _retrieve_chunks(
        self,
        context: AgentContext,
        organ: Organ,
    ) -> List[KnowledgeChunk]:
        """调用 KnowledgeAgent 检索 top-k 证据。

        注意：现有 :class:`KnowledgeAgent` 抽象不暴露 score 字段，
        因此本方法只返回 chunk 列表。置信度计算走
        :meth:`_compute_heuristic_confidence` 的启发式路径。

        T-GB-AGT-02：``context.retrieval_profile``（Adaptive RAG 档位）
        透传给知识智能体，使"标准/全量"的区别真正作用于检索强度
        （精排开关 / BM25 兜底 / LLM 增强 / 检索深度）。
        """
        kwargs: Dict[str, Any] = {
            "query": context.user_query,
            "organ": organ,
            "top_k": self._rag_top_k,
        }
        profile = getattr(context, "retrieval_profile", None)
        if profile and self._knowledge_accepts_profile:
            kwargs["profile"] = profile
        return await self._knowledge.query(**kwargs)

    async def _call_llm(
        self,
        context: AgentContext,
        prompt: str,
    ) -> str:
        """调用 LLM 生成文本（统一签名 — 与 app/inference/protocols.py 对齐）。

        Args:
            context: AgentContext（提供 request_id / session_id）。
            prompt: 已拼装好的完整 prompt。

        Returns:
            LLM 生成的纯文本。

        Raises:
            app.inference.protocols.InferenceError: LLM 调用失败。
        """
        request = InferenceRequest(
            prompt=prompt,
            max_tokens=self._max_tokens,
            request_id=context.request_id,
            session_id=context.session_id,
        )
        response = await self._inference.generate(request)
        return response.text or ""

    def _build_prompt(
        self,
        context: AgentContext,
        chunks: List[KnowledgeChunk],
        trace: List[str],
    ) -> str:
        """拼装完整 prompt：system 模板 + 知识 chunk + 用户 query。

        默认格式：
        ```
        {prompt_template}

        # 检索到的知识（top {n}）
        [1] {chunk_1_content} (source: {chunk_1_source_doc_id})
        [2] {chunk_2_content} (source: {chunk_2_source_doc_id})
        ...

        # 用户查询
        {user_query}

        # 影像元数据（可选）
        {imaging_metadata}
        ```

        子类可覆盖本方法自定义格式（如 few-shot 注入位置不同）。
        """
        parts: List[str] = [self._prompt_template, ""]
        parts.append(f"# 检索到的知识（top {len(chunks)}）")
        for idx, chunk in enumerate(chunks, start=1):
            source = chunk.source_doc_id or chunk.chunk_id
            parts.append(f"[{idx}] {chunk.content} (source: {source})")
        parts.append("")
        parts.append("# 用户查询")
        parts.append(context.user_query)
        if context.imaging_metadata:
            parts.append("")
            parts.append("# 影像元数据")
            parts.append(str(context.imaging_metadata))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 内部：置信度启发式（RAG 0.6 + LLM 0.4）
    # ------------------------------------------------------------------

    def _compute_heuristic_confidence(
        self,
        chunks: List[KnowledgeChunk],
        llm_text: str,
    ) -> float:
        """启发式置信度计算（公共基线，器官级可在 _parse_response 中覆盖）。

        公式：
            RAG 因子 = min(len(chunks) / 5, 1.0)        # 5 条满证据
            LLM 因子 = min(len(llm_text) / 500, 1.0)    # **500 字符饱满**
            置信度   = RAG * 0.6 + LLM * 0.4

        **500 字符饱和阈值的设计理由**：实际 few-shot 输出（含"分级"、
        "理由"、"总分"等结构化字段）通常 200-500 字符。原 200 字符过
        早达到 1.0，使 LLM 因子对 100-200 字符的"中等质量"输出丧失
        区分度。500 字符饱和阈值与典型 few-shot 输出长度对齐，能更好
        地反映输出完整度的差异。

        Returns:
            置信度（0.0 - 1.0）。
        """
        rag_factor = min(len(chunks) / max(self._rag_top_k, 1), 1.0)
        llm_factor = min(len(llm_text or "") / float(_LLM_TEXT_SATURATION_CHARS), 1.0)
        conf = rag_factor * _RAG_CONFIDENCE_WEIGHT + llm_factor * _LLM_CONFIDENCE_WEIGHT
        return round(conf, 4)

    # ------------------------------------------------------------------
    # 内部：D1 阈值检查（D1 偏离点修复）
    # ------------------------------------------------------------------

    def _check_disease_threshold(
        self,
        response: AgentResponse,
        disease_type: Optional[str],
    ) -> None:
        """按器官级/病种级阈值检查 final_conf 并设置 needs_review 标签。

        **D1 偏离点修复**（设计 §2.4）：
        - 优先级：先查 ``disease_thresholds[disease_type]``（病种级），
          若未配置则回退到 ``organ_threshold``（器官级）；
        - 若 ``response.confidence < threshold``，设置
          ``response.needs_review = True`` 并在 ``reasoning_trace`` 末尾追加
          ``below_<scope>_threshold: <conf> < <threshold>`` 标签；
        - 若 ``confidence == 0.0``（已降级）则跳过 — 降级响应本身已
          表示"不可用"，无须额外触发 needs_review。

        Args:
            response: 已完成 _parse_response 的响应对象。
            disease_type: 当前分级对应的病种（``None`` 或未知 → 走器官级）。
        """
        if response.confidence <= 0.0:
            return
        # 病种级优先 → 未配置则用器官级
        threshold: Optional[float] = None
        scope = "organ"
        if disease_type and disease_type in self._disease_thresholds:
            threshold = self._disease_thresholds[disease_type]
            scope = "disease"
        elif self._organ_threshold is not None:
            threshold = self._organ_threshold
            scope = "organ"
        if threshold is None:
            return
        if response.confidence < threshold:
            response.needs_review = True
            response.reasoning_trace.append(
                f"below_{scope}_threshold: {response.confidence:.4f} < {threshold}"
            )
            logger.info(
                "specialist_below_threshold",
                organ=self.get_organ().value,
                scope=scope,
                disease_type=disease_type,
                confidence=response.confidence,
                threshold=threshold,
            )

    # ------------------------------------------------------------------
    # 内部：D1+D2 公共后处理（invoke 末尾调用）
    # ------------------------------------------------------------------

    def _post_process(
        self,
        context: AgentContext,
        response: AgentResponse,
    ) -> None:
        """invoke 末尾的公共后处理（D1 阈值 + D2 used_evidence_ids 写入）。

        **D2 修正**：将 ``response.evidence_chunk_ids`` ``extend`` 到
        ``context.used_evidence_ids``，供 KB safety_critical 更新时做
        "哪些在途诊断使用了旧版 chunk"的精确回溯（02-核心流程 §2.4）。
        多专科联合诊断时所有 specialist 共享同一 ``AgentContext``，
        ``extend`` 操作按器官顺序累积；deduplication 由 KB 软失效层完成。

        **D1 修正**：器官级阈值检查（若 _parse_response 已调用
        _check_disease_threshold，则不再重复检查器官级 — 避免
        重复 trace 标签）。
        """
        # D2: 写入 used_evidence_ids（保留顺序，去重由调用方负责）
        if response.evidence_chunk_ids:
            context.used_evidence_ids.extend(response.evidence_chunk_ids)
        # D1: 器官级阈值（病种级由 _parse_response 调用 _check_disease_threshold）
        if (
            response.confidence > 0.0
            and self._organ_threshold is not None
            and response.confidence < self._organ_threshold
            and not response.needs_review
        ):
            response.needs_review = True
            response.reasoning_trace.append(
                f"below_organ_threshold: {response.confidence:.4f} < "
                f"{self._organ_threshold}"
            )

    # ------------------------------------------------------------------
    # 内部：M3/M5/M9 临床属性增强
    # ------------------------------------------------------------------

    def _enrich_clinical_attributes(
        self,
        response: AgentResponse,
        llm_text: str,
        chunks: List[KnowledgeChunk],
    ) -> None:
        """从 LLM 输出抽取结构化测量、后方回声与恶性概率（M3/M5/M9）。

        三条设计约束：

        1. **三重容错**：整体包在 try/except 内——增强能力失败绝不能影响诊断
           主流程；特征来源按「LLM 输出 → 检索到的知识 chunk」依次回退。
        2. **无依据就不写**：未命中任何特征时不写 ``malignancy_risk``（"依据
           不足"不能伪装成"概率为零"，前者要医生补资料，后者是明确判断）；
           未提取到尺寸时不写 ``measurement``（否则前端渲染全空表格）。
        3. **可解释**：每条写入都追加 reasoning_trace，含后方回声的 TI-RADS
           计分，医生可以据此核对模型为什么给出这个概率。
        """
        try:
            organ = self.get_organ().value
            features = extract_udaf_features(llm_text, organ=organ)
            fallback_text = " ".join(
                getattr(chunk, "content", "") or "" for chunk in chunks[:3]
            )
            if not features and fallback_text:
                features = extract_udaf_features(fallback_text, organ=organ)

            measurement = structured_measurement_from_text(llm_text, organ=organ)
            if measurement is None and fallback_text:
                measurement = structured_measurement_from_text(fallback_text, organ=organ)
            if measurement is not None:
                response.measurement = measurement
                response.reasoning_trace.append(
                    f"measurement_source -> {measurement.pixel_spacing_source}"
                )

            differentials = parse_differential_diagnoses(llm_text)
            if not differentials and fallback_text:
                differentials = parse_differential_diagnoses(fallback_text)
            if differentials:
                response.differential_diagnoses = differentials
                response.reasoning_trace.append(
                    f"differential_diagnoses -> {differential_summary(differentials)}"
                )

            posterior_term = pick_posterior_acoustic_term(features)
            if posterior_term:
                enum_value = posterior_acoustic_value(posterior_term)
                if enum_value:
                    response.posterior_acoustic = PosteriorAcoustic(enum_value)
                response.reasoning_trace.append(
                    f"posterior_acoustic -> {posterior_term}"
                )
                points = ti_rads_points_for_posterior(posterior_term)
                if points is not None:
                    response.reasoning_trace.append(
                        f"posterior_acoustic_ti_rads_points -> {points}"
                    )

            risk = compute_malignancy_risk(
                organ, list(features) + measurement_features(measurement)
            )
            if risk is not None:
                response.malignancy_risk = risk
                response.reasoning_trace.append(
                    f"malignancy_risk -> {risk.probability} ({risk.level.value})"
                )

            # M7：处置建议由显式规则表给出（不允许模型自由发挥），
            # 风险等级与特征作为输入；未建模器官返回空列表（表示"无内置规则"）
            actions = actions_for(
                organ,
                features,
                risk.level.value if risk is not None else None,
            )
            if actions:
                response.clinical_actions = actions
                response.reasoning_trace.append(
                    "clinical_actions -> " + "、".join(item.action_type for item in actions)
                )
        except Exception as exc:  # noqa: BLE001 - 增强失败不得影响诊断
            logger.warning(
                "clinical_attribute_enrichment_failed",
                organ=self.get_organ().value,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # 内部：默认 prompt 模板加载
    # ------------------------------------------------------------------

    def _load_default_prompt(self) -> str:
        """从 ``app/agents/prompts/{organ.value}.md`` 加载默认 system prompt。

        解析顺序（取最先可用者）：
        1. ``self.organ`` 类/实例属性（``Organ`` 枚举实例）；
        2. ``self.get_organ()`` 返回值；
        3. 类名启发式（去 ``SpecialistAgent`` 后缀）。

        Returns:
            完整 prompt 文本（含 markdown 内容）。

        Raises:
            FileNotFoundError: prompt 文件不存在（应当提供）。
        """
        organ_name: Optional[str] = None
        # 1) 类/实例属性 organ
        organ_attr = getattr(self, "organ", None)
        if isinstance(organ_attr, Organ):
            organ_name = organ_attr.value
        # 2) get_organ() 方法
        if organ_name is None:
            try:
                organ_value = self.get_organ()
                if isinstance(organ_value, Organ):
                    organ_name = organ_value.value
            except NotImplementedError:
                organ_name = None
        # 3) 类名启发式
        if organ_name is None:
            cls_name = self.__class__.__name__.lower()
            organ_name = cls_name.replace("specialistagent", "").strip("_") or cls_name

        prompt_path = _PROMPTS_DIR / f"{organ_name}.md"
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"prompt template not found: {prompt_path}. "
                f"Create {prompt_path.name} or pass prompt_template explicitly."
            )
        text = prompt_path.read_text(encoding="utf-8")
        return text


# ----------------------------------------------------------------------
# 占位实现（Phase 0A 兼容 — 保持与 Wave 1+2 dispatcher 集成）
# ----------------------------------------------------------------------


class PlaceholderSpecialistAgent(SpecialistAgent):
    """占位专科智能体（Phase 0A 用）。

    当请求被路由到某器官但真实 specialist 尚未实现时，dispatcher 调用
    本占位类并返回 ``specialist_pending`` 占位响应。本类不依赖 RAG/LLM，
    因此不继承 :class:`RAGSpecialistAgent`。

    行为：
    - ``invoke()`` 立即返回 ``confidence=0.0`` 的占位响应，
      ``content`` 标记为 ``"specialist_pending_implementation"``；
    - ``health_check()`` 永远返回 ``True``（占位不会失败，方便 dispatcher
      集成测试）。

    在 AGT-04/05/06 真实实现交付后，dispatcher lifespan 中替换为本类的
    实例为真实 specialist 实例即可。
    """

    organ: Organ

    def __init__(self, organ: Organ) -> None:
        self.organ = organ

    async def invoke(self, context: AgentContext) -> AgentResponse:
        start = time.monotonic()
        logger.info(
            "placeholder_specialist_invoked",
            organ=self.organ.value,
            request_id=context.request_id,
        )
        return AgentResponse(
            agent_name=f"placeholder_{self.organ.value}",
            organ=self.organ,
            content=(
                "[specialist_pending_implementation] "
                "this organ specialist is not yet wired in Phase 0A"
            ),
            confidence=0.0,
            reasoning_trace=[
                f"route -> {self.organ.value}",
                "specialist_pending_implementation",
            ],
            latency_ms=(time.monotonic() - start) * 1000.0,
        )

    def get_capability_tags(self) -> List[str]:
        return [f"{self.organ.value}_placeholder"]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        """占位 specialist 无资源可释放。"""
        return None


# ----------------------------------------------------------------------
# 工具函数：分级文本清洗（供器官级 _parse_response 复用）
# ----------------------------------------------------------------------

# 简单的空白规整 pattern
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_grading_text(text: str) -> str:
    """规整分级文本：去多余空白、保留必要分隔符。

    用于把 LLM 输出中的"TR 4"、"TR-4"、"tr4"等变体规整为统一形式，
    便于器官级 _parse_response 抽取分级码。

    Args:
        text: 原始文本。

    Returns:
        规整后的文本（保留大小写、去除多余空白）。
    """
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


__all__ = [
    "SpecialistAgent",
    "RAGSpecialistAgent",
    "PlaceholderSpecialistAgent",
    "DEFAULT_RAG_TOP_K",
    "normalize_grading_text",
    "_LLM_TEXT_SATURATION_CHARS",
]
