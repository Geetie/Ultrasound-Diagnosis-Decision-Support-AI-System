"""AGT 智能体协议定义模块 (T-GA-AGT-01).

本模块定义多智能体系统（MAS）的核心接口契约和数据模型，是整个 Agent 子系统
的基础抽象层。

核心组件：
- ``AgentContext``：智能体调用时的共享上下文（请求/会话/器官/历史消息等）。
- ``AgentResponse``：智能体调用返回的统一响应（内容/置信度/证据/推理链路/耗时）。
- ``Agent``：通用智能体抽象基类（ABC），所有 specialist 智能体的统一入口。
- ``DispatcherAgent``：中央调度智能体接口（route + aggregate）。
- ``KnowledgeAgent``：知识查询智能体接口（query + 两层缓存）。
- ``MonitorAgent``：流程监控智能体接口（track_diagnosis + detect_anomaly）。

设计原则（与 ``app/inference/protocols.py`` / ``app/rag/protocols.py`` 保持一致）：
- 使用 ABC 模式实现多后端适配（Phase 0A 为单实现，Phase 0B+ 可引入不同后端）。
- 类型安全的请求/响应数据结构，使用 Pydantic 进行严格校验。
- 智能体接口设计为「单一入口」（``invoke``），降低上层调用复杂度。
- 错误码/异常体系统一通过 Python 原生 ``Exception`` 向上抛，调用方自行降级。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import (
    ClinicalAction,
    CrossModalityHint,
    DiagnosisRequest,
    DiagnosisResult,
    DifferentialDiagnosis,
    MalignancyRisk,
    Measurement,
    Organ,
    PatientExplanation,
    PosteriorAcoustic,
    ResearchMetrics,
)


# ----------------------------------------------------------------------
# 共享数据模型
# ----------------------------------------------------------------------


class AgentContext(BaseModel):
    """智能体调用时的共享上下文。

    Attributes:
        request_id: 请求唯一标识（与 ``DiagnosisResult.request_id`` 对应）。
        session_id: 会话标识（与 ``DiagnosisRequest.session_id`` 对应）。
        organ: 目标器官（``Organ`` 枚举）。
        user_query: 用户原始查询文本。
        imaging_metadata: 可选的影像元数据（DICOM 元信息、模态、帧序号等）。
        prior_messages: 多轮对话中的历史消息（``{"role": ..., "content": ...}``）。
        used_evidence_ids: 专科智能体在诊断过程中**实际引用**的知识 chunk
            ID 列表。**设计要求（02-核心流程与知识库 §2.4）**：KB
            ``safety_critical`` 更新时依赖此字段做"哪些在途诊断使用了
            旧版 chunk"的精确回溯与取消。**写入时机**：每个 specialist
            在 :meth:`invoke` 末尾将本次响应实际使用的 evidence_chunk_ids
            ``extend`` 到本字段。**多专科联合诊断**：dispatcher 应在调用
            specialist 前对所有 specialist 共享同一 ``AgentContext``
            （Python 对象传引用），以便多专科响应按器官顺序累积写入。
        retrieval_profile: T-GB-AGT-02 Adaptive RAG 检索档位
            （``RetrievalProfile.as_retrieval_kwargs()`` 的产物，形如
            ``{"use_reranking": False, "top_k": 10, ...}``）。由诊断管线按
            路由判定注入，专科智能体透传给
            ``KnowledgeAgent.query(profile=...)``，使"标准/全量"档位真正
            作用于检索强度。``None`` 表示沿用 RAG 默认档位（与引入该
            字段前的行为完全一致）。
    """
    request_id: str
    session_id: str
    organ: Organ
    user_query: str
    retrieval_profile: Optional[dict[str, Any]] = None
    imaging_metadata: Optional[dict[str, Any]] = None
    prior_messages: List[dict[str, Any]] = Field(default_factory=list)
    used_evidence_ids: List[str] = Field(default_factory=list)

    @field_validator("request_id", "session_id")
    @classmethod
    def _id_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("id field must not be empty")
        return v

    @field_validator("user_query")
    @classmethod
    def _user_query_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("user_query must not be empty or whitespace-only")
        return v


class AgentResponse(BaseModel):
    """智能体调用返回的统一响应。

    Attributes:
        agent_name: 智能体名称（用于埋点和日志聚合，如 ``"thyroid_specialist"``）。
        organ: 智能体响应的器官（dispatcher 透传请求 organ，specialist 固定为自身器官）。
        content: 智能体返回的文本内容。
        confidence: 置信度（0.0 - 1.0）。当 specialist 失败时可为 0.0。
        evidence_chunk_ids: 引用的知识 chunk ID 列表。
        reasoning_trace: 推理链路描述，用于审计和调试。
        latency_ms: 调用总耗时（毫秒）。
        needs_review: 是否需要人工复核（specialist 自标，FUS 层不覆盖）。
            由 specialist 在 confidence 低于器官级/病种级阈值时设置 ``True``。
            该字段在 FUS 融合层 (T-GA-FUS-01) 设计契约中明确：specialist
            标记 ``needs_review=True`` → 融合结果 ``FusionResult.needs_review``
            必须为 True 且 ``review_reason="specialist_flagged"``，由
            ``app.fusion.service.FusionEngineImpl.fuse`` 在第 5 步合并。
        disease_code: UDT 病种编码（可选，跨器官关联校验使用）。
            来自 ``00a-术语表.md`` UDT 编码体系（如
            ``UDT-LIVER-HCC-ADVANCED``、``UDT-CARDIAC-HF-REDUCED``）；
            specialist 未填时由 FUS 层基于 content 启发式推导。
        used_evidence_ids: specialist 实际使用过的 evidence chunk ID
            子集（KB safety_critical 精确撤销依据）。
            区别于 ``evidence_chunk_ids``（所有引用），``used_evidence_ids``
            仅记录最终结论实际依赖的 chunk，KB 升级时仅需重新评估
            ``used_evidence_ids`` 命中的旧结论。
        deviation_severity: 分级偏差跨越临床决策边界的严重程度。**FUS 阶段
            消费**（02-核心流程 §3.1）：由 FUS-01 融合层基于 specialist
            分级与知识库预期/历史结论的偏差判定写入，可能取值：
            ``"none"``（一致） / ``"minor"``（同一档内微小偏差） /
            ``"major"``（跨档偏差，如 TR3→TR4 — 不容忍 → 强制人工复核）。
            specialist 自身不写入（仅占位 ``None``），保留给 FUS 轨道。
    """
    agent_name: str
    organ: Optional[Organ] = None
    content: str = ""
    confidence: float = 0.0
    evidence_chunk_ids: List[str] = Field(default_factory=list)
    reasoning_trace: List[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    # P0-1 修复：补齐 FUS 透传契约
    needs_review: bool = False
    # P2-1 增强：跨器官关联规则使用
    disease_code: Optional[str] = None
    # P2-1 增强：KB safety_critical 精确撤销
    used_evidence_ids: List[str] = Field(default_factory=list)
    # FUS 分级偏差判定（specialist 仅占位 None，由 FUS-01 融合层写入）
    deviation_severity: Optional[str] = None
    # === 医生直接信息需求（M1~M12）结构化输出 ===
    # 全部可选且带默认值：既有构造点（生产 20 余处 + 测试 30 余处）均使用
    # 关键字参数，扩展不会破坏任何调用方；未实现的专科智能体保持默认空值，
    # 前端按"无数据不渲染"处理。
    # 结构化测量（M3）
    measurement: Optional[Measurement] = None
    # 鉴别诊断清单，按可能性降序（M4）
    differential_diagnoses: List[DifferentialDiagnosis] = Field(default_factory=list)
    # 恶性概率，与 confidence 语义不同，需分开展示（M5）
    malignancy_risk: Optional[MalignancyRisk] = None
    # 转诊/处置建议（M7）
    clinical_actions: List[ClinicalAction] = Field(default_factory=list)
    # 患者解释话术（M10，默认不入正式报告）
    patient_explanation: Optional[PatientExplanation] = None
    # 科研量化指标（M11，前端默认折叠）
    research_metrics: Optional[ResearchMetrics] = None
    # 跨模态对照提示（M12）
    cross_modality_hints: List[CrossModalityHint] = Field(default_factory=list)
    # 后方回声特征（M9，UDAF 属性；同时供 M5 概率计算使用）
    posterior_acoustic: Optional[PosteriorAcoustic] = None

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError("confidence must be in [0.0, 1.0]")
        return v

    @field_validator("deviation_severity")
    @classmethod
    def _deviation_severity_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in {"none", "minor", "major"}:
            raise ValueError(
                "deviation_severity must be one of {None, 'none', 'minor', 'major'}"
            )
        return v

    @field_validator("agent_name")
    @classmethod
    def _agent_name_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("agent_name must not be empty")
        return v


# ----------------------------------------------------------------------
# 抽象接口
# ----------------------------------------------------------------------


class Agent(ABC):
    """通用智能体抽象基类。

    所有具体智能体（specialist、dispatcher、knowledge、monitor）均通过 ``invoke()``
    暴露唯一入口，便于上层调度器统一调用和埋点。
    """

    @abstractmethod
    async def invoke(self, context: AgentContext) -> AgentResponse:
        """执行智能体调用。

        Args:
            context: 调用上下文。

        Returns:
            ``AgentResponse``：统一响应对象。

        Raises:
            Exception: 任何内部错误都直接向上抛；调用方负责降级。
        """
        raise NotImplementedError

    @abstractmethod
    def get_capability_tags(self) -> List[str]:
        """返回智能体的能力标签列表。

        用于：
        - 路由表构建（dispatcher 可基于能力标签动态选择 specialist）；
        - 健康检查 / 能力发现。

        Returns:
            能力标签字符串列表（如 ``["thyroid", "tirads"]``）。
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查。

        Returns:
            True 表示健康，False 表示不健康。
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """释放智能体持有的所有资源（缓存、连接、内存态状态等）。

        lifespan 关闭阶段会调用本方法。**实现要求**：
        - 任何异常都必须吞掉（warning 日志），不能阻塞 lifespan 关闭；
        - 调用后智能体不应再被使用（语义上"已关闭"）。
        """
        raise NotImplementedError


class DispatcherAgent(ABC):
    """中央调度智能体接口（T-GA-AGT-02）。

    负责：
    1. ``route()``：根据 ``DiagnosisRequest`` 选择目标 organ（Phase 0A 直接读取
       ``target_organ`` 字段；Phase 0B+ 可加入基于文本意图识别的智能路由）。
    2. ``aggregate()``：融合多个 ``AgentResponse`` 为单一结果（Phase 0A 使用
       简单平均；Phase 0B+ 由 FUS 轨道替换为基于一致性的加权融合）。
    3. **P2-8 修复** ``route_crag_query()``：CRAG 补充检索的中央路由
       （设计文档 12-系统性修订实施指南.md:171）：
       Self-RAG 失败 → ``dispatcher.route_crag_query(organ, query)`` →
       内部委派给 ``KnowledgeAgent.query()``（享受两层缓存 + 负载均衡） →
       返回 KnowledgeChunk 列表。
    """

    @abstractmethod
    async def route(self, request: DiagnosisRequest) -> Organ:
        """根据请求选择目标器官。

        Args:
            request: 诊断请求。

        Returns:
            ``Organ``：目标器官枚举值。
        """
        raise NotImplementedError

    @abstractmethod
    async def aggregate(self, specialist_responses: List[AgentResponse]) -> AgentResponse:
        """融合多个 specialist 响应。

        Args:
            specialist_responses: 各专科智能体返回的响应列表（可能为空）。

        Returns:
            融合后的 ``AgentResponse``。当输入为空时返回 ``confidence=0.0`` 的
            占位响应。
        """
        raise NotImplementedError

    @abstractmethod
    async def route_crag_query(
        self,
        organ: Organ,
        query: str,
        top_k: int = 20,
        failure_category: str = "unknown",
    ) -> List["KnowledgeChunk"]:
        """CRAG 补充检索的中央路由（P2-8 修复）。

        设计文档 12-系统性修订实施指南.md:171 要求 CRAG 链路：
        ``Self-RAG 失败 → 中央调度 → 知识查询智能体 → RAG → 返回结果``。

        默认实现策略：内部直接调用 ``self._knowledge_agent.query()``。
        复杂场景可重写为多跳路由 / 跨器官协调。

        Args:
            organ: 目标器官（用于过滤和缓存键）。
            query: 增强后的查询文本（含失败原因关键词）。
            top_k: 返回的 chunk 上限。
            failure_category: Self-RAG 失败原因分类。

        Returns:
            :class:`KnowledgeChunk` 列表，按相关性降序。

        Raises:
            RuntimeError: 当未配置 ``knowledge_agent`` 时。
        """
        raise NotImplementedError


class KnowledgeAgent(ABC):
    """知识查询智能体接口（T-GA-AGT-03）。

    负责：
    1. 封装 RAG Service（``app.rag.service.RAGService``）；
    2. 实现两层缓存（与 ``01-系统架构设计.md`` §2.3 对齐）：
       - **L1 — Redis 共享缓存，TTL = 24 小时（86400s）**；
       - **L2 — 进程内 LRU 缓存，容量 ≤ 50，TTL = 1 小时（3600s）**；
    3. 缓存键格式 ``kb:cache:{organ.value}:v{kb_version}:{sha1(query)[:16]}``，
       KB 版本号嵌入键名实现自然失效（版本变更 → 旧键自然消失）；
    4. 击穿 / 穿透 / 雪崩三项防护（单 key 互斥锁、负缓存、TTL 抖动）；
    5. 缓存命中 / 未命中 / Redis 不可用降级。
    """

    @abstractmethod
    async def query(
        self,
        query: str,
        organ: Organ,
        top_k: int = 10,
        profile: Optional[dict[str, Any]] = None,
    ) -> List[KnowledgeChunk]:
        """查询知识库。

        Args:
            query: 用户查询文本（非空）。
            organ: 目标器官（用于过滤和缓存键）。
            top_k: 返回的 chunk 上限。
            profile: T-GB-AGT-02 检索档位（
                ``RetrievalProfile.as_retrieval_kwargs()`` 的产物）。
                传入时其中的 ``use_reranking`` / ``use_llm_enhancement`` /
                ``use_bm25_fallback`` / ``top_k`` 会真正作用于底层
                ``RetrievalRequest``。``None`` 表示使用默认档位。

        Returns:
            ``KnowledgeChunk`` 列表，按相关性降序。

        Raises:
            ValueError: query 为空或 top_k <= 0。
            RAGError: RAG pipeline 失败（调用方自行降级）。
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查。

        Returns:
            True 当且仅当 RAG 依赖可达。
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """释放智能体持有的资源（清空 L1/L2 缓存、清理击穿锁等）。

        lifespan 关闭阶段会调用本方法。**实现要求**：
        - 任何异常都必须吞掉（warning 日志），不能阻塞 lifespan 关闭；
        - 调用后智能体不应再被使用（语义上"已关闭"）；
        - Redis 客户端连接**不**在本方法中关闭（由全局
          ``app.config.redis.RedisPool.close()`` 统一负责）。
        """
        raise NotImplementedError


class MonitorAgent(ABC):
    """流程监控智能体接口（T-GA-AGT-07）。

    负责：
    1. ``track_diagnosis()``：记录每次诊断的 latency / confidence / 异常标签；
    2. ``detect_anomaly()``：在请求进入调度前预检请求是否明显异常。
    """

    @abstractmethod
    async def track_diagnosis(
        self,
        request: DiagnosisRequest,
        response: DiagnosisResult,
    ) -> None:
        """追踪一次完整诊断。

        实现 3 条规则：
        - R1: ``confidence_score < 0.3`` → ``needs_review``；
        - R2: ``processing_time_ms > 5000`` → ``performance_degraded``；
        - R3: 同一 organ 连续 10 次失败 → ``system_anomaly``。
        """
        raise NotImplementedError

    @abstractmethod
    async def detect_anomaly(self, request: DiagnosisRequest) -> Optional[str]:
        """对请求做异常预检。

        Phase 0A 仅做最简检查（image_ids 为空 / target_organ 缺失），后续可扩展。
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查。

        MonitorAgent 本身无外部依赖（仅做内存态窗口维护和指标埋点），
        所以健康检查通常返回 ``True``。保留该接口便于上层在 lifespan 启动期
        统一调用 ``health_check()`` 验证所有智能体的可用性。

        Returns:
            True 表示健康，False 表示不健康。
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """释放智能体持有的资源（清空 organ 级别滑动窗口、停止后台任务等）。

        lifespan 关闭阶段会调用本方法。**实现要求**：
        - 任何异常都必须吞掉（warning 日志），不能阻塞 lifespan 关闭；
        - 调用后智能体不应再被使用（语义上"已关闭"）。
        """
        raise NotImplementedError


__all__ = [
    "Agent",
    "AgentContext",
    "AgentResponse",
    "DispatcherAgent",
    "KnowledgeAgent",
    "MonitorAgent",
]
