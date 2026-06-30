# T-GA-RAG-01/02/03 — 完整 RAG 检索管线 实施计划

> 轨道: **RAG** | Wave 2 | 预计工作量: 5-7 天 | 依赖: ✅ T-GA-DATA-01（knowledge_chunks） + ✅ T-GA-EMB-02（EmbeddingService + VectorIndexClient） + ✅ T-GA-AGT-01（startup 顺序占位）
>
> 任务拆分: T-GA-RAG-01 查询增强 / T-GA-RAG-02 多模态并行检索 / T-GA-RAG-03 精排+BM25 兜底
>
> 验收: 混合检索召回率 ≥ 90%（1000 条）/ 精排 Top5 准确率 ≥ 85% / 扩展后召回率提升 ≥ 10% / 检索 P99 < 100ms / 单元测试覆盖率 ≥ 90%

---

## 0. 假设与权衡清单（Step 1: Think Before Coding）

### 查询增强（T-GA-RAG-01）
1. **同义词库来源**: 复用 [`UDAFVocabulary`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/vocabulary.py)（organ / echogenicity / boundary / morphology / vascularity / calcification / TI-RADS / LI-RADS / heart_structure / heart_condition / heart_function）。同义词已在 `_reverse_lookup` 表中，无需重建。
2. **扩展层数**: 3 层（**L1 同义词** + **L2 上位词 / 类别** + **L3 相关特征**）。L2 = `get_terms_by_category()`；L3 = 同器官内 `get_terms_by_organ()` 的相关 term 集合。
3. **LLM 扩展**: **可选**。当 `use_llm_enhancement=True` 时，调用 Agnes API 改写 query；失败/超时回退到仅 UDAF。LLM 改写结果用 prompt 模板约束为「关键词列表 + 器官 + 关键特征」。
4. **器官识别**: 复用 [`UDTParser._detect_organ`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/udt_parser.py#L167-L176) + `UDTParser.parse()` 提取 normalized_terms（不重复实现，调用现有方法）。
5. **不调用 LLM 做扩展是默认行为**: 与 RAG 主流做法一致，UDAF 同义词 + 器官过滤已经能覆盖 80% 召回。LLM 仅作"扩展增强"层（Agnes API key 免费），开关由 `settings.rag_use_llm_enhancement` 控制。

### 多模态检索（T-GA-RAG-02）
6. **向量检索**: Dense 使用 [`BgeM3MedicalService.embed_text()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/bge_m3.py) → [`create_index_client_from_settings()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/index_factory.py) → `search()`。Query vector 维度 1024。
7. **稀疏检索**: BM25 over `knowledge_chunks.content` 字段（**与精排兜底共用同一索引**，避免重复构建）。通过 `app.knowledge.protocols.KnowledgeChunk` 走数据库读取。
8. **融合算法**: **RRF (Reciprocal Rank Fusion)**，公式 `score(d) = Σ 1/(k + rank_i(d))`，k=60（标准取值）。Dense + Sparse 双向融合，**不**做线性加权。
9. **多模态查询**: 当 `request.image_data` 不为 None 时，并行调用 [`UltrasoundClipService.embed_image()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/ultrasound_clip.py) 拿到 768d 向量，单独检索 `ultrasound_chunks` 集合（如果存在）后与文本检索结果 RRF 融合。无 image 集合时跳过图像分支（不报错）。
10. **器官过滤**: 通过 Qdrant `filter={"organ": "thyroid"}` 实现，遵循 [`QdrantVectorIndex._build_filter()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/qdrant_client.py#L106-L147) 语法。`request.organ` 透传。
11. **后端降级**: 直接复用 [`create_index_client_from_settings()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/index_factory.py) — Qdrant 不可用时自动降级 PGVector，零侵入。

### 精排（T-GA-RAG-03）
12. **精排选型**: **bge-reranker-base**（278M 参数，约 100ms / 20 文档 / CPU，BASE 级别准确率 85% 区间）。**HTTP 客户端模式**（与 `BgeM3MedicalService` 一致）：端口 `settings.bge_reranker_port` 默认 8005，端点 `/v1/rerank`。
13. **精排降级**: HTTP 不可用时，**降级到 LLM 改写后的精排启发式**（用 cosine 相似度 + 词重叠度合成 score），并打 WARN 日志，**不**直接失败。
14. **Top-K**: 输入 TopK=20（来自 retriever），输出 TopK=5（`settings.rag_rerank_output_top_k`）。
15. **分数必须返回**: `RetrievalResult` 中每 chunk 必带 `rerank_score`（未精排时为 retriever 原 score）。

### BM25 兜底（T-GA-RAG-03）
16. **触发条件**: 满足任一即触发：
    - retriever 返回空（0 命中）
    - `top1_score < settings.rag_bm25_min_top1_score`（默认 0.3）
    - `retrieved_count < settings.rag_bm25_min_results`（默认 3）
17. **库选型**: `rank_bm25`（v0.2.2，主流轻量实现）— 添加到 `requirements.txt`。
18. **索引缓存**: 进程内懒加载 + 启动时校验版本；`kb_version` 不匹配时强制重建。`max_chunks=10000` 硬上限（超出截断 + WARN）。
19. **来源**: 直接从 `knowledge_chunks` 表读 `content` + `chunk_id` + `organ` + `kb_version`，无中间表。

### 缓存策略
20. **三层缓存**（每层独立 TTL，**不**过度复杂化）：
    - L1: BM25 索引（启动后不变，kb_version 校验触发重建）
    - L2: Dense embedding 缓存（key = `sha256(query_text)`，TTL = `settings.rag_query_embedding_cache_ttl` 默认 1h）
    - L3: Rerank 缓存（key = `sha256(query + chunk_ids)`，TTL 默认 1h）
21. **缓存后端**: 进程内 LRU（默认）+ Redis 可选（开关 `settings.rag_use_redis_cache`）。本期**只实现进程内 LRU**，Redis 留作 RAG-04 扩展。

### 性能预算
22. **P99 < 100ms**（不含网络）：Dense 30ms + Sparse 20ms + RRF 5ms + Rerank 30ms（含 HTTP 5ms）+ 编排 15ms = 100ms
23. **召回率提升 ≥ 10%**: 验证方法 = 在 1000 条测试集上对比 `retrieval_recall(raw_query)` 与 `retrieval_recall(enhanced_query)`。
24. **精排 Top5 准确率 ≥ 85%**: 验证方法 = 1000 条测试集，Top5 命中 ground-truth chunk_id 视为正确。

### 依赖与配置
25. **新增依赖**: `rank-bm25==0.2.2`（仅 BM25 需要）。`httpx` 已存在（用于 reranker HTTP）。**不**引入 `transformers` / `torch`（避免与 llama.cpp / vllm 启动冲突）。
26. **新增配置项**: `settings.rag_*`（约 15 项，见 §4 Settings 章节）。`vector_index_backend` / `qdrant_*` 已存在但未声明，**需补全**。
27. **API Key 注入**: Agnes API key 从 `settings.agnes_api_key` 读取（来源: 环境变量 `AGNES_API_KEY`），**不**硬编码。Agnes 不可用时 use_llm_enhancement 自动 fallback 到 False 并打 INFO 日志。
28. **不修改任何 embedding / knowledge / inference 模块**: 仅引用现有 Protocol，避免破坏 DATA / EMB 轨道的契约。

---

## 1. 当前状态分析（Current State）

### 已实现（✅ 依赖就绪）
- [`app/embedding/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/protocols.py) — `EmbeddingService` ABC + `TextEmbeddingRequest/Response` + `SparseEmbeddingResponse` + `VectorIndexClient` ABC + `VectorRecord` / `SearchResult`
- [`app/embedding/bge_m3.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/bge_m3.py) — `BgeM3MedicalService`（1024 维文本向量，HTTP 客户端模式）
- [`app/embedding/ultrasound_clip.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/ultrasound_clip.py) — `UltrasoundClipService`（768 维图像向量）
- [`app/embedding/qdrant_client.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/qdrant_client.py) — Qdrant 实现，支持 `filter={"organ": "..."}` + 多值/范围语法
- [`app/embedding/pgvector_client.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/pgvector_client.py) — PGVector 降级实现
- [`app/embedding/index_factory.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/index_factory.py) — `create_index_client()` 工厂 + Qdrant→PGVector 自动降级
- [`app/knowledge/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/protocols.py) — `KnowledgeChunk` Pydantic 模型（chunk_id / kb_version / organ / content / metadata / embedding）
- [`app/knowledge/vocabulary.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/vocabulary.py) — `UDAFVocabulary`（同义词、上位词、器官枚举、`iter_synonym_mappings` / `get_synonyms` / `get_terms_by_category` / `get_terms_by_organ`）
- [`app/knowledge/udt_parser.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/udt_parser.py) — `UDTParser.parse()`（器官识别 + TI/LI-RADS + measurements）
- [`app/knowledge/hybrid_indexer.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/hybrid_indexer.py) — `PGVectorHybridIndexer.vector_search()`（已支持 organ 过滤的向量检索）
- [`app/models/database.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/models/database.py#L235-L253) — `KnowledgeChunkModel`（`knowledge_chunks` 表，chunk_id / kb_version / organ / content / meta / embedding / source_doc_id / section_path / token_count）
- [`app/inference/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/protocols.py) — Protocol/ABC 风格参考（Error + ErrorCode + ABC + 数据类）
- [`app/main.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py#L32-L119) — FastAPI `lifespan`（inference_engine / speech_engine / gpu_schedule 已就位，需追加 `rag_service`）
- [`app/config/settings.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py) — `Settings` 类（端口冲突校验、`compact` / `full` 模式校验）
- [`pyproject.toml`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/pyproject.toml) — pytest markers（含 `track_rag`）+ asyncio_mode=auto
- [`tests/conftest.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/conftest.py) — 全局 fixtures（redis_client、HSM 环境变量）
- `tests/unit/rag/test_knowledge.py`（已存在）— 覆盖 UDAF / UDT，**不**修改，沿用作 vocabulary/parser 回归
- 已有 EMB-02 设置项 `embedding_model` / `embedding_dim` / `ultrasound_clip_*` / `bge_m3_*`（**不**重复声明）

### 待实现（本任务产物）
- [ ] [`app/rag/__init__.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/__init__.py) — 包导出
- [ ] [`app/rag/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/protocols.py) — `RetrievalRequest` / `RetrievalResult` / `RAGService` ABC + 错误码
- [ ] [`app/rag/query_enhancer.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/query_enhancer.py) — T-GA-RAG-01 三层查询扩展（UDAF + 可选 LLM）
- [ ] [`app/rag/retriever.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/retriever.py) — T-GA-RAG-02 Dense + Sparse (BM25) + 多模态 + RRF
- [ ] [`app/rag/reranker.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/reranker.py) — T-GA-RAG-03 bge-reranker-base HTTP 客户端 + 启发式降级
- [ ] [`app/rag/bm25_fallback.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/bm25_fallback.py) — T-GA-RAG-03 BM25 兜底（懒加载 + kb_version 校验 + max 10000 chunks）
- [ ] [`app/rag/service.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/service.py) — `RAGServiceImpl` 编排器（P99 埋点 + health_check）
- [ ] [`app/rag/llm_enhancer.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/llm_enhancer.py) — Agnes 客户端封装（可选，失败回退）
- [ ] [`app/config/settings.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py) 追加 — `rag_*` 配置 + `vector_index_backend` / `qdrant_*`（缺失项补全）+ `agnes_*` + `bge_reranker_*`
- [ ] [`requirements.txt`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/requirements.txt) 追加 — `rank-bm25==0.2.2`
- [ ] [`app/main.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py) lifespan — 注入 `app.state.rag_service` + `await rag_service.health_check()`
- [ ] `tests/unit/rag/test_query_enhancer.py` — 查询增强（UDAF 三层 / 器官识别 / 覆盖率 / LLM 回退 mock）
- [ ] `tests/unit/rag/test_retriever.py` — Dense / Sparse / Hybrid / 多模态 / 器官过滤（Mock Qdrant + BM25）
- [ ] `tests/unit/rag/test_reranker.py` — 精排分数 / TopK 重排 / HTTP 失败降级（Mock bge-reranker）
- [ ] `tests/integration/rag/test_rag_e2e.py` — 端到端：1000 条测试 query + 召回率/准确率/P99 验证
- [ ] `archive_of_achivement/archive_phase0A_rag_{日期}/PROJECT_ARCHIVE.md` — 归档报告（混合检索 vs 单一检索对比 + 召回率/准确率/延迟报告）

### 现有约定（沿用）
- **logger 风格**: `structlog.get_logger("app.rag.<module>")`
- **Protocol 风格**: ABC + `@abstractmethod` + Pydantic `BaseModel` + Enum 错误码（参照 [`inference/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/protocols.py) 和 [`embedding/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/protocols.py)）
- **测试风格**: `pytest-asyncio`（asyncio_mode=auto）+ `AsyncMock` + `pytest.mark.track_rag` marker
- **HTTP 客户端**: 复用 `app.common.http_client_base.BaseHttpClient` + `app.embedding.base.BaseHttpEmbeddingService` 模式（httpx 异步 + 连接池 + 5s health_check）
- **Settings 风格**: Pydantic BaseSettings + `populate_by_name=True` + `extra="ignore"`
- **不重复实现**: 任何 UDAF / UDT / Embedding / VectorIndex 逻辑都通过现有 Protocol 引用

---

## 2. 实施步骤（Proposed Changes）

### Step 1 — 补全 `app/config/settings.py` + `requirements.txt`

**文件**: [`app/config/settings.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py)（修改）、[`requirements.txt`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/requirements.txt)（追加）

**Why 先做**: 所有 RAG 模块都依赖 settings，配置项不全会导致后步骤频繁返工。

**Settings 追加**（在 `embedding_*` 块之后，约 L160-171）：

```python
# Vector index backend (EMB-02 已实现但未在 settings 声明)
vector_index_backend: str = 'qdrant'   # 'qdrant' | 'pgvector'
qdrant_host: str = 'localhost'
qdrant_port: int = 6333
qdrant_grpc_port: int = 6334
qdrant_timeout_seconds: float = 30.0
qdrant_api_key: str = ''
vector_index_auto_fallback: bool = True

# RAG (T-GA-RAG-01/02/03)
rag_use_query_enhancement: bool = True
rag_use_reranking: bool = True
rag_use_bm25_fallback: bool = True
rag_use_multimodal: bool = True
rag_collection: str = 'knowledge_chunks'         # Qdrant collection name
rag_top_k: int = 20                              # retriever 召回 TopK
rag_rerank_output_top_k: int = 5                 # 精排输出 TopK
rag_min_score_threshold: float = 0.5
rag_bm25_min_top1_score: float = 0.3             # 触发兜底的 top1 阈值
rag_bm25_min_results: int = 3                    # 触发兜底的命中数阈值
rag_bm25_max_chunks: int = 10000                 # BM25 索引硬上限
rag_query_embedding_cache_ttl_seconds: int = 3600
rag_use_redis_cache: bool = False                # 进程内 LRU 默认
rag_p99_warning_threshold_ms: float = 80.0
rag_p99_critical_threshold_ms: float = 100.0

# bge-reranker (T-GA-RAG-03)
bge_reranker_host: str = 'localhost'
bge_reranker_port: int = 8005
bge_reranker_timeout_seconds: float = 5.0
bge_reranker_model: str = 'bge-reranker-base'
bge_reranker_health_check_timeout_seconds: float = 3.0

# Agnes LLM (T-GA-RAG-01 可选增强)
agnes_api_key: str = ''           # 来自环境变量 AGNES_API_KEY
agnes_api_base: str = 'https://agnes-api.example.com/v1'
agnes_api_model: str = 'agnes-flash'
agnes_timeout_seconds: float = 3.0
```

端口冲突校验（[`settings.py:325-340`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L325-L340)）需要把 `bge_reranker_port` 加入 `ports` 列表。

**requirements.txt 追加**：

```text
rank-bm25==0.2.2
```

**Why**: `rank_bm25` 是 BM25 兜底的核心库；其他依赖（httpx、structlog、pydantic、asyncpg、pgvector）已存在。

---

### Step 2 — 创建 `app/rag/__init__.py` + `app/rag/protocols.py`

**文件**: [`app/rag/__init__.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/__init__.py)（新建）、[`app/rag/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/protocols.py)（新建）

**接口**（完全遵循任务规约）：

```python
# app/rag/protocols.py
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator

from app.knowledge.protocols import KnowledgeChunk


class RAGErrorCode(str, Enum):
    ENHANCEMENT_FAILED = "E5001_RAG_ENHANCEMENT_FAILED"
    RETRIEVAL_FAILED = "E5001_RAG_RETRIEVAL_FAILED"
    RERANK_FAILED = "E5001_RAG_RERANK_FAILED"
    BM25_INDEX_FAILED = "E5001_RAG_BM25_INDEX_FAILED"
    TIMEOUT = "E5001_RAG_TIMEOUT"
    HEALTH_CHECK_FAILED = "E5001_RAG_HEALTH_CHECK_FAILED"


class RAGError(Exception):
    def __init__(self, error_code: RAGErrorCode, message: str,
                 detail: str | None = None, retryable: bool = False,
                 retry_strategy: str = "no_retry"):
        self.error_code = error_code
        self.message = message
        self.detail = detail
        self.retryable = retryable
        self.retry_strategy = retry_strategy
        super().__init__(f"[{error_code.value}] {message}")


class RetrievalMethod(str, Enum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    BM25_FALLBACK = "bm25_fallback"


class RetrievalRequest(BaseModel):
    query: str
    organ: Optional[str] = None
    image_data: Optional[bytes] = None
    image_format: str = "dicom"
    top_k: int = 20
    use_query_enhancement: bool = True
    use_reranking: bool = True
    use_bm25_fallback: bool = True
    use_multimodal: bool = True
    use_llm_enhancement: bool = False       # T-GA-RAG-01 可选
    min_score_threshold: float = 0.5
    kb_version: Optional[str] = None
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def query_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v

    @field_validator("top_k")
    @classmethod
    def top_k_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("top_k must be positive")
        return v


class ScoredChunk(BaseModel):
    chunk: KnowledgeChunk
    score: float
    rerank_score: Optional[float] = None
    retrieval_source: RetrievalMethod


class RetrievalResult(BaseModel):
    chunks: List[ScoredChunk] = Field(default_factory=list)
    retrieval_method: RetrievalMethod
    enhanced_query: Optional[str] = None       # T-GA-RAG-01 增强后 query
    llm_enhanced: bool = False                 # 是否触发了 LLM 增强
    triggered_bm25_fallback: bool = False      # 是否触发 BM25 兜底
    latency_ms: float = 0.0
    p99_violation: bool = False                # 埋点用

    @property
    def top_score(self) -> float:
        return self.chunks[0].score if self.chunks else 0.0

    @property
    def has_results(self) -> bool:
        return len(self.chunks) > 0


class RAGService(ABC):
    @abstractmethod
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...
```

**`__init__.py`** 导出所有公共类（与 `embedding/__init__.py` 一致）。

**Why**: Protocol 是 RAG 轨道的入口契约，下游 AGT 轨道（T-GA-AGT-03 知识查询智能体）依赖此 ABC。错误码独立命名空间（`E5001`）避免与其他轨道冲突。

---

### Step 3 — 实现 T-GA-RAG-01 `app/rag/llm_enhancer.py`（Agnes 客户端）

**文件**: [`app/rag/llm_enhancer.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/llm_enhancer.py)（新建）

**Why 单独文件**: LLM 增强是**可选**能力，与主查询增强解耦，便于测试和降级。

**接口**：

```python
class LLMEnhancer(ABC):
    @abstractmethod
    async def enhance(self, original_query: str,
                      udaf_expansion: List[str],
                      organ: Optional[str]) -> Optional[str]: ...
    @abstractmethod
    async def health_check(self) -> bool: ...


class AgnesLLMEnhancer(LLMEnhancer):
    """使用 Agnes API 改写 query。失败/超时/未配置时返回 None。"""
    def __init__(self, api_key: str, api_base: str, model: str,
                 timeout_seconds: float = 3.0):
        ...
    async def enhance(self, ...) -> Optional[str]:
        # 1) 校验 api_key 非空
        # 2) 构造 prompt（system: 你是医学查询改写助手，输出 JSON；user: 改写下面 query）
        # 3) httpx POST 到 {api_base}/chat/completions
        # 4) 解析返回的 JSON {"rewritten_query": "...", "keywords": [...]}
        # 5) 失败/超时/解析异常 → 记录 INFO 日志并返回 None
```

**Prompt 模板**（内置，**不**读取外部文件）：

```text
你是一名医学超声领域的查询改写助手。
任务：将用户的原始查询改写为更适合知识库检索的形式。
约束：
1) 保留器官名和关键特征词
2) 补充同义词（如低回声 ↔ hypoechoic）
3) 输出 JSON 格式：{"rewritten_query": "...", "keywords": ["..."]}
原始查询：{query}
UDAF 扩展词：{udaf_expansion}
器官：{organ}
```

**Why**: 强制 JSON 输出便于解析；约束 1-2 保证 LLM 改写不偏离医学事实。失败回退到 None，由 query_enhancer 决定走纯 UDAF 路径。

---

### Step 4 — 实现 T-GA-RAG-01 `app/rag/query_enhancer.py`

**文件**: [`app/rag/query_enhancer.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/query_enhancer.py)（新建）

**接口**：

```python
class QueryEnhancer:
    def __init__(self, vocabulary: UDAFVocabulary,
                 udt_parser: UDTParser,
                 llm_enhancer: Optional[LLMEnhancer] = None,
                 max_expansion_per_layer: int = 5):
        self.vocab = vocabulary
        self.udt_parser = udt_parser
        self.llm_enhancer = llm_enhancer
        self.max_per_layer = max_expansion_per_layer

    async def enhance(self, query: str,
                      organ_hint: Optional[str] = None) -> EnhancedQuery:
        """
        Returns: EnhancedQuery(original=..., enhanced=..., organ=..., layers=..., llm_enhanced=bool)
        """
        # 1) UDT 解析 → organ + normalized_terms
        # 2) L1: 对 normalized_terms 调 vocab.get_synonyms() 去重
        # 3) L2: 调 vocab.get_terms_by_category(category) 限制每层 max_per_layer 个
        # 4) L3: 调 vocab.get_terms_by_organ(organ) 限制每层 max_per_layer 个
        # 5) 拼接: enhanced = f"{query} {' '.join(L1 + L2 + L3)}"
        # 6) 如果 llm_enhancer 可用: try await self.llm_enhancer.enhance(...)
        #    - 成功: enhanced = llm_result, llm_enhanced=True
        #    - 失败/None: 保留 UDAF 结果, llm_enhanced=False
        # 7) 返回 EnhancedQuery
```

**EnhancedQuery 数据类**（放在 `protocols.py`）：

```python
class EnhancedQuery(BaseModel):
    original: str
    enhanced: str
    organ: Optional[str] = None
    layer1_synonyms: List[str] = Field(default_factory=list)
    layer2_hypernyms: List[str] = Field(default_factory=list)
    layer3_related: List[str] = Field(default_factory=list)
    llm_enhanced: bool = False
```

**验收自检**（在 `enhance()` 末尾）：计算 `coverage = vocab.coverage_rate(normalized_terms)`，记录到 `structlog` 用于回溯召回率。

**Why 复用 UDAF / UDT**: 任务规约明确"**不得重复实现**"。[`UDAFVocabulary.get_synonyms()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/vocabulary.py#L369-L383) 已返回同义词列表；[`get_terms_by_category()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/vocabulary.py#L385-L398) 提供 L2；[`get_terms_by_organ()`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/vocabulary.py#L401-L415) 提供 L3。UDT 解析器已正确实现器官识别 + TI/LI-RADS 抽取。

---

### Step 5 — 实现 T-GA-RAG-02 `app/rag/retriever.py`（多模态并行检索）

**文件**: [`app/rag/retriever.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/retriever.py)（新建）

**核心类**：

```python
class Retriever:
    def __init__(self,
                 vector_client: VectorIndexClient,        # 来自 create_index_client
                 embedding_service: EmbeddingService,     # BgeM3MedicalService
                 ultrasound_clip: Optional[EmbeddingService] = None,  # 多模态
                 bm25_index: Optional[BM25Index] = None,   # 共用 bm25_fallback 实例
                 rrf_k: int = 60):
        ...

    async def retrieve(self,
                       request: RetrievalRequest,
                       enhanced_query: str) -> RetrievalResult:
        # 1) 并行:
        #    task_a: dense = await self._dense_search(enhanced_query, request)
        #    task_b: sparse = await self._sparse_search(enhanced_query, request)
        #    task_c (if request.image_data): image = await self._image_search(request)
        # 2) RRF 融合 (dense + sparse + image?)
        # 3) 截断到 top_k=20
        # 4) 返回 RetrievalResult(retrieval_method=HYBRID)
```

**子方法**：

- `_dense_search(query, request) → List[(chunk_id, score, KnowledgeChunk)]`
  - 调用 `embedding_service.embed_text(TextEmbeddingRequest(texts=[query]))` → 取 embeddings[0]
  - `vector_client.search(collection=request.kb_version or settings.rag_collection, query_vector=..., top_k=request.top_k, filter={"organ": request.organ} if organ else None)`
  - 拿 `SearchResult` 后用 `embedding_service` 不够，需要 `_resolve_chunk(search_result.id)` → 调 `PGVectorHybridIndexer.get_by_id` 拿完整 `KnowledgeChunk`

- `_sparse_search(query, request) → List[(chunk_id, score, KnowledgeChunk)]`
  - 调 `self.bm25_index.search(query, top_k=request.top_k, organ_filter=request.organ)`
  - 由 `bm25_fallback.py` 提供 `BM25Index` 类（见 Step 7）

- `_image_search(request) → List[(chunk_id, score, KnowledgeChunk)]`
  - 调用 `ultrasound_clip.embed_image(ImageEmbeddingRequest(image_data=..., image_format=...))`
  - `vector_client.search(collection="ultrasound_chunks", query_vector=..., top_k=request.top_k)`
  - 若 `ultrasound_chunks` collection 不存在（Qdrant 返回 `UnexpectedResponse`）→ 记录 INFO + 跳过（**不**报错）

- `_rrf_fuse(result_lists: List[List[SearchHit]], k=60) → List[SearchHit]`
  - 公式: `fused_score(d) = sum(1 / (k + rank_i(d)))` for each rank_i where d appears
  - 重复 chunk_id 累加分数，按 fused_score 降序排序

- `_resolve_chunk(chunk_id) → KnowledgeChunk`
  - 调 `PGVectorHybridIndexer.get_by_id(chunk_id, session)` — **不**直接访问 DB

**异常处理**（按模式匹配）：

| 异常 | 处理 |
|------|------|
| `EmbeddingError(CONNECTION_FAILED)` | retry 1 次（5s），仍失败 → 返回空 dense + WARN 日志 |
| `EmbeddingError(TIMEOUT)` | 返回空 dense + WARN 日志 |
| `VectorIndexError(SEARCH_FAILED)` | 返回空 dense + WARN 日志（不影响 sparse） |
| `UnexpectedResponse(404, "Collection 'ultrasound_chunks' not found")` | 静默跳过图像分支 |
| `AsyncSession` 异常 | 上抛 RAGError(RETRIEVAL_FAILED) |

**Why RRF 而非线性加权**: RRF 不需要 score 校准（不同检索器的分数量纲不同），k=60 是 RRF 标准取值（参考 Cormack et al. 2009）。

---

### Step 6 — 实现 T-GA-RAG-03 `app/rag/reranker.py`（Cross-Encoder 精排）

**文件**: [`app/rag/reranker.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/reranker.py)（新建）

**核心类**：

```python
class RerankService(ABC):
    @abstractmethod
    async def rerank(self, query: str,
                     chunks: List[KnowledgeChunk],
                     top_k: int) -> List[ScoredChunk]: ...
    @abstractmethod
    async def health_check(self) -> bool: ...


class BgeRerankerService(RerankService):
    """bge-reranker-base HTTP 客户端（参照 BaseHttpEmbeddingService 风格）。"""
    def __init__(self, base_url: str, model: str,
                 timeout_seconds: float = 5.0,
                 health_check_timeout_seconds: float = 3.0):
        # 使用 httpx.AsyncClient + 连接池（max_connections=10, keepalive=5）
        ...
    async def rerank(self, query, chunks, top_k) -> List[ScoredChunk]:
        # 1) POST {base_url}/v1/rerank
        #    body: {"query": query, "documents": [c.content for c in chunks], "top_k": top_k, "model": self.model}
        # 2) 解析 response: {"results": [{"index": 0, "relevance_score": 0.95}, ...]}
        # 3) 映射回 ScoredChunk，保持原 chunk，rerank_score=relevance_score，score=max(原 score, rerank_score)
        # 4) 按 rerank_score 降序，截断 top_k
        # 5) 失败/超时 → 抛出 RerankError，由 service.py 决定降级


class HeuristicReranker(RerankService):
    """降级实现：BM25 词重叠度 + cosine 相似度（仅当 chunks 都有 embedding 时）。"""
    async def rerank(self, query, chunks, top_k) -> List[ScoredChunk]:
        # 1) 分词: jieba（可选）或简单 split
        # 2) 词重叠度 = len(query ∩ chunk_content) / len(query ∪ chunk_content)
        # 3) cosine = 1 - cosine_distance(embedding, ...)  (如果 chunk.embedding 存在)
        # 4) score = 0.5 * jaccard + 0.5 * cosine
        # 5) 排序截断
```

**调用约定**（由 `service.py` 编排）：

```python
# service.py 中的精排逻辑
try:
    reranked = await self.reranker.rerank(query, candidates, top_k=output_top_k)
except (httpx.TimeoutException, httpx.ConnectError, RerankError) as e:
    logger.warning("rerank_failed_using_heuristic", error=str(e))
    reranked = await self.heuristic_reranker.rerank(query, candidates, top_k=output_top_k)
```

**Why 双实现**: bge-reranker-base 是高精度首选；HTTP 不可用时（开发环境 / 部署简化场景）不直接失败，用启发式降级保证服务可用。**不**静默吞错，必有 WARN 日志。

---

### Step 7 — 实现 T-GA-RAG-03 `app/rag/bm25_fallback.py`（BM25 兜底 + 共享 BM25 索引）

**文件**: [`app/rag/bm25_fallback.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/bm25_fallback.py)（新建）

**Why 单独文件**: BM25 索引在 retriever 和 bm25_fallback 两处使用，**必须共享**避免重复构建。

**核心类**：

```python
@dataclass
class BM25Hit:
    chunk_id: str
    score: float
    chunk: KnowledgeChunk


class BM25Index:
    """懒加载 BM25 索引，从 knowledge_chunks 表构建。"""
    def __init__(self, session_factory: Callable[[], AsyncSession],
                 max_chunks: int = 10000):
        self._session_factory = session_factory
        self._max_chunks = max_chunks
        self._index: Optional[BM25Okapi] = None
        self._chunk_id_map: List[str] = []         # index → chunk_id
        self._kb_version: Optional[str] = None
        self._lock = asyncio.Lock()
        self._built_at: float = 0.0

    async def ensure_built(self, kb_version: Optional[str] = None) -> None:
        """懒加载 + 版本校验。kb_version 变化时强制重建。"""
        async with self._lock:
            if self._index is None or (kb_version and kb_version != self._kb_version):
                await self._build(kb_version)

    async def _build(self, kb_version: Optional[str]) -> None:
        """SELECT chunk_id, content, organ, kb_version, ... FROM knowledge_chunks
           WHERE kb_version = $1 (or ALL if None) LIMIT $max_chunks"""
        # 1) 打开 AsyncSession
        # 2) SELECT chunk_id, content, organ, kb_version FROM knowledge_chunks
        #    WHERE (kb_version = $1 OR $1 IS NULL) ORDER BY chunk_id LIMIT $max_chunks
        # 3) 对每个 content 做 jieba 分词（或简单 split，按中英混合处理）
        # 4) BM25Okapi(corpus) 构建索引
        # 5) 记录 _kb_version, _built_at
        # 6) WARN 如果实际行数 == max_chunks: bm25_index_truncated

    def search(self, query: str, top_k: int = 20,
               organ_filter: Optional[str] = None) -> List[BM25Hit]:
        """同步方法（rank_bm25 本身是 CPU bound）。"""
        # 1) 分词 query
        # 2) bm25.get_scores(tokenized_query) → array of scores
        # 3) 过滤 organ_filter (None = all)
        # 4) argsort 降序，截断 top_k
        # 5) 返回 List[BM25Hit]，按 score 降序

    async def health_check(self) -> bool:
        """检查索引是否就绪（_index is not None and len(_chunk_id_map) > 0）。"""
        return self._index is not None and len(self._chunk_id_map) > 0

    async def close(self) -> None:
        self._index = None
        self._chunk_id_map = []


class BM25Fallback:
    """BM25 兜底触发器。"""
    def __init__(self, bm25_index: BM25Index,
                 min_top1_score: float = 0.3,
                 min_results: int = 3):
        self._index = bm25_index
        self._min_top1_score = min_top1_score
        self._min_results = min_results

    def should_trigger(self, retriever_result: RetrievalResult) -> bool:
        if not retriever_result.has_results:
            return True
        if retriever_result.top_score < self._min_top1_score:
            return True
        if len(retriever_result.chunks) < self._min_results:
            return True
        return False

    async def fallback_search(self, query: str, top_k: int,
                              organ: Optional[str]) -> List[ScoredChunk]:
        hits = self._index.search(query, top_k=top_k, organ_filter=organ)
        return [
            ScoredChunk(
                chunk=h.chunk, score=h.score,
                rerank_score=None, retrieval_source=RetrievalMethod.BM25_FALLBACK
            )
            for h in hits
        ]
```

**分词策略**（简单方案，不引入 jieba 依赖以减小体积）：
- 中英混合：先按 Unicode 范围切中文连续字符（`[\u4e00-\u9fa5]+`）作为一个 token，英文/数字按 `[^a-zA-Z0-9]+` split。
- 示例: `"低回声结节"` → `["低回声", "结节"]`；`"TI-RADS 4A nodule"` → `["ti", "rads", "4a", "nodule"]`
- 同时支持大小写不敏感

**Why 简单分词**: BM25 本身对分词不敏感（IDF 统计权重），且医疗 query 长度短（典型 5-20 词），过度优化分词收益小。后续 T-GA-RAG-04 可换 jieba + 医学词典。

---

### Step 8 — 实现 `app/rag/service.py`（编排器）

**文件**: [`app/rag/service.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/service.py)（新建）

**核心类**：

```python
class RAGServiceImpl(RAGService):
    def __init__(self,
                 query_enhancer: QueryEnhancer,
                 retriever: Retriever,
                 reranker: RerankService,
                 heuristic_reranker: RerankService,
                 bm25_fallback: BM25Fallback,
                 p99_warning_ms: float = 80.0,
                 p99_critical_ms: float = 100.0):
        ...

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        start = time.monotonic()
        # 1) Query Enhancement
        if request.use_query_enhancement:
            enhanced = await self.query_enhancer.enhance(request.query, request.organ)
            query_for_retrieval = enhanced.enhanced
        else:
            query_for_retrieval = request.query
            enhanced = None

        # 2) Retrieval (Dense + Sparse + Optional Image, RRF)
        result = await self.retriever.retrieve(request, query_for_retrieval)

        # 3) Reranking (with heuristic fallback)
        if request.use_reranking and result.has_results:
            try:
                reranked = await self.reranker.rerank(
                    query_for_retrieval, [sc.chunk for sc in result.chunks],
                    top_k=request.top_k  # 传 20 给 rerank
                )
                result.chunks = reranked[:self._rerank_output_top_k]
            except Exception as e:
                logger.warning("rerank_failed_using_heuristic", error=str(e))
                reranked = await self.heuristic_reranker.rerank(
                    query_for_retrieval, [sc.chunk for sc in result.chunks],
                    top_k=self._rerank_output_top_k
                )
                result.chunks = reranked

        # 4) BM25 Fallback
        if request.use_bm25_fallback and self.bm25_fallback.should_trigger(result):
            logger.info("bm25_fallback_triggered", reason=...)
            fallback_chunks = await self.bm25_fallback.fallback_search(
                query_for_retrieval, top_k=self._rerank_output_top_k,
                organ=request.organ
            )
            result.chunks = fallback_chunks
            result.retrieval_method = RetrievalMethod.BM25_FALLBACK
            result.triggered_bm25_fallback = True

        # 5) Finalize
        latency_ms = (time.monotonic() - start) * 1000
        result.latency_ms = latency_ms
        if enhanced:
            result.enhanced_query = enhanced.enhanced
            result.llm_enhanced = enhanced.llm_enhanced
        result.p99_violation = latency_ms > self._p99_critical_ms
        if result.p99_violation:
            logger.warning("rag_p99_violation", latency_ms=latency_ms)
        elif latency_ms > self._p99_warning_ms:
            logger.info("rag_p99_warning", latency_ms=latency_ms)
        return result

    async def health_check(self) -> bool:
        # 检查所有依赖：embedding_service, vector_client, reranker, bm25_index
        # 任意一个失败 → 整体 False（但只打 WARN）
        ...

    async def close(self) -> None:
        # 关闭 bm25_index, vector_client, embedding_service, reranker
        ...
```

**P99 埋点**（P99 监控在 RAG-04 / OBS 轨道集成，本期只做阈值检测 + 结构化日志）：

```python
logger.warning(
    "rag_p99_violation",
    request_id=request.request_id,
    latency_ms=latency_ms,
    retrieval_method=result.retrieval_method.value,
    triggered_bm25_fallback=result.triggered_bm25_fallback,
)
```

**Why 三阶段编排**: 增强 → 检索 → 精排 → 兜底 是 RAG 主流 pipeline；每阶段可独立 mock 便于测试；BM25 兜底是最后一道闸门。

---

### Step 9 — Wire into `app/main.py` lifespan

**文件**: [`app/main.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py)（修改）

**lifespan 中追加**（在 `gpu_schedule` 初始化之后，约 L57）：

```python
# RAG service
from app.embedding.bge_m3 import BgeM3MedicalService
from app.embedding.ultrasound_clip import UltrasoundClipService
from app.embedding.index_factory import create_index_client_from_settings
from app.knowledge.udt_parser import UDTParser
from app.knowledge.vocabulary import UDAFVocabulary
from app.rag.bm25_fallback import BM25Index, BM25Fallback
from app.rag.llm_enhancer import AgnesLLMEnhancer
from app.rag.query_enhancer import QueryEnhancer
from app.rag.reranker import BgeRerankerService, HeuristicReranker
from app.rag.retriever import Retriever
from app.rag.service import RAGServiceImpl

vector_client = await create_index_client_from_settings()
embedding_svc = BgeM3MedicalService(
    base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}",
    timeout_seconds=settings.embedding_timeout_seconds,
)
clip_svc = UltrasoundClipService(
    base_url=f"http://{settings.ultrasound_clip_host}:{settings.ultrasound_clip_port}",
    timeout_seconds=settings.embedding_timeout_seconds,
) if settings.rag_use_multimodal else None

bm25_index = BM25Index(
    session_factory=AsyncSessionLocal,
    max_chunks=settings.rag_bm25_max_chunks,
)
await bm25_index.ensure_built(kb_version=settings.phase0a_mock_kb_version)

bm25_fb = BM25Fallback(bm25_index, min_top1_score=settings.rag_bm25_min_top1_score,
                       min_results=settings.rag_bm25_min_results)

llm_enhancer = AgnesLLMEnhancer(
    api_key=settings.agnes_api_key,
    api_base=settings.agnes_api_base,
    model=settings.agnes_api_model,
    timeout_seconds=settings.agnes_timeout_seconds,
) if settings.rag_use_llm_enhancement else None

query_enhancer = QueryEnhancer(
    vocabulary=UDAFVocabulary(),
    udt_parser=UDTParser(),
    llm_enhancer=llm_enhancer,
)

bge_reranker = BgeRerankerService(
    base_url=f"http://{settings.bge_reranker_host}:{settings.bge_reranker_port}",
    model=settings.bge_reranker_model,
    timeout_seconds=settings.bge_reranker_timeout_seconds,
)
heuristic_reranker = HeuristicReranker()

retriever = Retriever(
    vector_client=vector_client,
    embedding_service=embedding_svc,
    ultrasound_clip=clip_svc,
    bm25_index=bm25_index,
)

rag_service = RAGServiceImpl(
    query_enhancer=query_enhancer,
    retriever=retriever,
    reranker=bge_reranker,
    heuristic_reranker=heuristic_reranker,
    bm25_fallback=bm25_fb,
    p99_warning_ms=settings.rag_p99_warning_threshold_ms,
    p99_critical_ms=settings.rag_p99_critical_threshold_ms,
)
app.state.rag_service = rag_service

healthy = await rag_service.health_check()
if not healthy:
    logger.warning("rag_service_health_check_failed_at_startup")
```

**lifespan 退出时**（在 `inference_engine.close()` 后）追加：

```python
if hasattr(app.state, "rag_service") and app.state.rag_service:
    await app.state.rag_service.close()
```

**Why lifespan 而非 module-level**: 复用 `RedisPool.get_client()` / `create_inference_engine_from_settings()` 的初始化模式，保持代码风格一致。

---

### Step 10 — 编写单元测试

**目录**: `tests/unit/rag/`（**已存在** `__init__.py`）

#### 10.1 `tests/unit/rag/test_query_enhancer.py`

```python
pytestmark = [pytest.mark.track_rag]

class TestQueryEnhancer:
    def test_l1_synonyms_expansion(self):
        # 输入: "低回声结节"
        # 断言: layer1_synonyms 包含 "回声减低"、"hypoechoic"
        ...

    def test_l2_category_expansion(self):
        # 输入: "低回声" (echogenicity)
        # 断言: layer2_hypernyms 包含同 category 词（"高回声"、"等回声"）
        ...

    def test_l3_organ_expansion(self):
        # 输入: "甲状腺结节"
        # 断言: organ="thyroid", layer3_related 包含 "TI-RADS 4A" 等 thyroid terms
        ...

    def test_organ_detection_uses_udt_parser(self):
        # mock UDTParser.parse → 返回 organ="liver"
        # 断言: enhance 返回 organ="liver"
        ...

    def test_llm_enhancer_called_when_available(self):
        # mock LLMEnhancer.enhance → 返回 "liver nodule hypoechoic"
        # 断言: enhanced == "liver nodule hypoechoic", llm_enhanced=True
        ...

    def test_llm_enhancer_failure_falls_back_to_udaf(self):
        # mock LLMEnhancer.enhance → 抛异常
        # 断言: 仍返回 UDAF enhanced, llm_enhanced=False
        ...

    def test_coverage_rate_logged(self, caplog):
        # 断言: caplog 包含 "query_enhancement_coverage"
        ...
```

#### 10.2 `tests/unit/rag/test_retriever.py`

```python
pytestmark = [pytest.mark.track_rag]

class TestRetriever:
    @pytest.fixture
    def mock_vector_client(self):
        # Mock VectorIndexClient.search → 返回固定 SearchResult 列表
        ...

    @pytest.fixture
    def mock_embedding(self):
        # Mock EmbeddingService.embed_text → 返回固定 1024d 向量
        ...

    @pytest.fixture
    def mock_bm25(self):
        # Mock BM25Index.search → 返回固定 BM25Hit 列表
        ...

    async def test_dense_search(self, mock_vector_client, mock_embedding):
        # 断言: 调 vector_client.search 一次，filter 包含 organ
        ...

    async def test_sparse_search(self, mock_bm25):
        # 断言: 调 bm25.search 一次，organ_filter 透传
        ...

    async def test_hybrid_rrf_fusion(self, mock_vector_client, mock_bm25):
        # 断言: dense + sparse 融合，重复 chunk_id 累加 RRF 分数
        # 计算手算 RRF 期望值
        ...

    async def test_multimodal_when_image_provided(self, mock_clip_svc):
        # 断言: clip_svc.embed_image 被调用，ultrasound_chunks collection 检索
        ...

    async def test_multimodal_skipped_when_no_image(self, mock_clip_svc):
        # 断言: clip_svc.embed_image 未被调用
        ...

    async def test_organ_filter_passed(self, mock_vector_client):
        # 断言: filter={"organ": "thyroid"} 透传
        ...

    async def test_ultrasound_collection_not_found_silent_skip(self):
        # mock vector_client.search 抛 UnexpectedResponse(404)
        # 断言: 不抛异常，返回无 image 贡献的 result
        ...
```

#### 10.3 `tests/unit/rag/test_reranker.py`

```python
pytestmark = [pytest.mark.track_rag]

class TestBgeRerankerService:
    @pytest.fixture
    def mock_httpx(self):
        # Mock httpx.AsyncClient.post → 返回固定 JSON
        ...

    async def test_rerank_top_k(self, mock_httpx):
        # 断言: 返回 top_k 个 ScoredChunk，rerank_score 透传
        ...

    async def test_rerank_score_persisted(self, mock_httpx):
        # 断言: ScoredChunk.rerank_score 等于 response.relevance_score
        ...

    async def test_health_check(self, mock_httpx):
        # 断言: /health 返回 200 → True，否则 False
        ...

    async def test_http_failure_raises(self, mock_httpx):
        # mock post 抛 ConnectError
        # 断言: rerank 抛 RerankError
        ...


class TestHeuristicReranker:
    async def test_jaccard_score(self):
        # 断言: score = jaccard(query, chunk_content)
        ...

    async def test_combined_with_cosine(self):
        # 提供 embedding 字段
        # 断言: score = 0.5*jaccard + 0.5*cosine
        ...
```

#### 10.4 `tests/unit/rag/test_bm25_fallback.py`（新增）

```python
class TestBM25Index:
    async def test_lazy_build(self):
        # 第一次 search → 触发 _build
        # 第二次 search → 复用 _index
        ...

    async def test_kb_version_change_rebuilds(self):
        # build with v1.0.0
        # ensure_built with v2.0.0 → 重建
        ...

    async def test_max_chunks_truncation(self, caplog):
        # mock session 返回 > max_chunks 行
        # 断言: 实际索引 max_chunks，caplog 包含 WARN
        ...


class TestBM25Fallback:
    def test_trigger_when_empty(self):
        # result.chunks=[]
        # 断言: should_trigger=True
        ...

    def test_trigger_when_top1_low(self):
        # result.top_score=0.2 < 0.3
        # 断言: should_trigger=True
        ...

    def test_no_trigger_when_sufficient(self):
        # 5 chunks, top_score=0.8
        # 断言: should_trigger=False
        ...
```

#### 10.5 `tests/integration/rag/test_rag_e2e.py`

```python
pytestmark = [pytest.mark.track_rag, pytest.mark.integration]


class TestRAGE2E:
    @pytest.fixture(scope="class")
    async def rag_service(self):
        # 初始化完整 RAGServiceImpl（使用 mock 后端 + 内置 BM25）
        # 包含 1000 条 mock knowledge_chunks
        ...

    async def test_recall_improvement_via_enhancement(self, rag_service):
        # raw = await rag_service.retrieve(req(use_query_enhancement=False))
        # enh = await rag_service.retrieve(req(use_query_enhancement=True))
        # 计算 recall@5 for both
        # 断言: enh_recall - raw_recall >= 0.10
        ...

    async def test_hybrid_recall_meets_target(self, rag_service):
        # 1000 条测试 query
        # 断言: recall@5 >= 0.90
        ...

    async def test_rerank_top5_accuracy(self, rag_service):
        # 1000 条 ground-truth 标注
        # 断言: precision@5 >= 0.85
        ...

    async def test_p99_latency(self, rag_service):
        # 1000 次 retrieve
        # 断言: P99 latency < 100ms
        ...

    async def test_bm25_fallback_triggers_on_low_top1(self, rag_service):
        # 构造低分 query
        # 断言: triggered_bm25_fallback=True
        ...

    async def test_health_check(self, rag_service):
        # 断言: await rag_service.health_check() == True
        ...
```

**集成测试 skip 条件**: 当 `RAG_E2E_ENABLED` 环境变量未设置时 skip（默认 skip，与 LLM_TEST_MODE 风格一致）。

---

### Step 11 — 验证

按以下顺序执行验证：

1. **单元测试**:
   ```bash
   python -m pytest tests/unit/rag -v --cov=app.rag --cov-report=term-missing
   ```
   - 预期: 全部通过，覆盖率 ≥ 90%

2. **集成测试**（需 `RAG_E2E_ENABLED=1`）:
   ```bash
   RAG_E2E_ENABLED=1 python -m pytest tests/integration/rag -v
   ```
   - 预期: 全部通过，recall@5 ≥ 0.90, precision@5 ≥ 0.85, p99 < 100ms

3. **健康检查**:
   ```bash
   python -c "from app.main import app; import asyncio; asyncio.run(_test_health())"
   # 或启动 uvicorn 后 GET /api/v1/health
   ```
   - 预期: rag_service.health_check() == True

4. **手动验证**（可选，QA 用）:
   ```bash
   python scripts/test_rag_manual.py --query "甲状腺低回声结节 TI-RADS 4A"
   # 预期: 返回 Top-5 chunks，包含 TI-RADS 4A 相关内容
   ```

5. **Lint + Type Check**:
   ```bash
   ruff check app/rag/ tests/unit/rag/ tests/integration/rag/
   mypy app/rag/
   ```
   - 预期: 0 error

---

### Step 12 — 提交 + 归档

1. **Git commit**:
   ```bash
   git add app/rag/ app/config/settings.py app/main.py requirements.txt \
           tests/unit/rag/ tests/integration/rag/ scripts/test_rag_manual.py
   git commit -m "feat(RAG): T-GA-RAG-01/02/03 - 完整RAG管线"
   ```

2. **归档报告**: 在 `archive_of_achivement/archive_phase0A_rag_{日期}/PROJECT_ARCHIVE.md` 写归档报告，包含：
   - 召回率/准确率/延迟报告（混合检索 vs Dense-only vs Sparse-only 对比表）
   - 扩展前后召回率对比（UDAF 单独 / UDAF+LLM 对比）
   - BM25 兜底触发次数统计
   - 已知问题 + 后续 RAG-04 路线

---

## 3. 验收清单

- [ ] **T-GA-RAG-01**: 查询增强召回率提升 ≥ 10%（UDAF vs Raw）
- [ ] **T-GA-RAG-02**: 混合检索召回率 ≥ 90%（1000 条测试集）
- [ ] **T-GA-RAG-03**: 精排后 Top5 准确率 ≥ 85%
- [ ] **BM25 兜底**: 触发条件正确（empty / top1<0.3 / count<3）
- [ ] **检索 P99 < 100ms**（在 1000 次请求中验证）
- [ ] **单元测试覆盖率 ≥ 90%**（`app/rag/` 全包）
- [ ] **端到端集成测试通过**（`tests/integration/rag/test_rag_e2e.py`）
- [ ] **health_check 返回 True**（lifespan 启动后）
- [ ] **不破坏 DATA / EMB 轨道**（既有测试全部通过）

---

## 4. 风险与依赖

### 风险
1. **bge-reranker 服务未部署** → 启发式降级 + WARN 日志（不失败）
2. **Agnes API 限流/超时** → 返回 None，回退到纯 UDAF 扩展（INFO 日志）
3. **BM25 大语料慢** → `max_chunks=10000` 硬上限 + WARN；超出截断
4. **Qdrant 不可用** → 复用 `create_index_client_from_settings()` 自动降级 PGVector
5. **lifespan 启动顺序** → RAG 在 `inference_engine` 之后初始化（避免端口冲突）

### 后续依赖
- **T-GA-AGT-03（知识查询智能体）**: 依赖 `RAGService.retrieve()` 接口
- **T-GA-FUS-02（融合层 Self-RAG）**: 可能复用 `EnhancedQuery` 做 hallucination 检测
- **T-GA-OBS-04（RAG 监控面板）**: 依赖 `p99_violation` 字段 + structlog 日志

### 不在本期范围
- **RAG-04**: Redis 分布式缓存、Jieba 分词、向量量化（int8）、HNSW 调参
- **AGT-03**: 知识查询智能体（依赖 RAG）
- **RAG API endpoint**: 暴露给前端的 `/api/v1/rag/search` 端点（建议在 AGT 轨道统一暴露，避免重复）

---

## 5. 实施时间线（建议 5-7 天）

| Day | 任务 | 验证 |
|-----|------|------|
| 1 | Step 1-2: settings + protocols | `python -c "from app.rag.protocols import RAGService"` |
| 2 | Step 3-4: llm_enhancer + query_enhancer | `pytest tests/unit/rag/test_query_enhancer.py -v` |
| 3-4 | Step 5-7: retriever + reranker + bm25_fallback | `pytest tests/unit/rag/test_retriever.py tests/unit/rag/test_reranker.py -v` |
| 5 | Step 8-9: service.py + main.py 集成 | `pytest tests/unit/rag -v` 全过 |
| 6 | Step 10-11: e2e 测试 + 验证 | `RAG_E2E_ENABLED=1 pytest tests/integration/rag -v` |
| 7 | Step 12: 归档 + 文档 + 调优 | `archive/.../PROJECT_ARCHIVE.md` 完成 |

---

## 6. 关键文件参考

- 入口契约: [`app/rag/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/protocols.py)（本任务新建）
- 编排器: [`app/rag/service.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/service.py)（本任务新建）
- Lifespan 注入: [`app/main.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py#L32-L119)
- Settings 扩展: [`app/config/settings.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py)
- 复用: [`app/knowledge/vocabulary.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/vocabulary.py) / [`udt_parser.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/knowledge/udt_parser.py)
- 复用: [`app/embedding/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/protocols.py) / [`bge_m3.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/bge_m3.py) / [`index_factory.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/index_factory.py)
- 协议参考: [`app/inference/protocols.py`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/inference/protocols.py)
- 同轨道既有计划: [`.trae/documents/T-GA-DATA-01-kb-builder.md`](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/.trae/documents/T-GA-DATA-01-kb-builder.md)
