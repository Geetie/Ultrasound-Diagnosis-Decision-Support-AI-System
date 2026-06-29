# T-GA-RAG-01/02/03 — 完整 RAG 管线收尾执行计划

> 轨道: **RAG** | Wave 2 收尾 | 阶段: 修复阻塞 → 测试通过 → 归档 → 提交 | 今日: 2026-06-29
>
> 关键发现: 主体实现已全部完成,但存在 3 个导致测试无法运行的 **关键阻塞**,必须在执行前修复。

---

## 1. 当前状态盘点 (Current State)

### ✅ 已完成（无需重写）
| 文件 | 用途 | 状态 |
|------|------|------|
| [app/rag/protocols.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/protocols.py) | RAGErrorCode / RAGError / RetrievalMethod / RetrievalRequest / ScoredChunk / EnhancedQuery / RetrievalResult / RAGService ABC | ✅ |
| [app/rag/llm_enhancer.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/llm_enhancer.py) | LLMEnhancer ABC + AgnesLLMEnhancer(httpx 客户端) | ✅ |
| [app/rag/query_enhancer.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/query_enhancer.py) | QueryEnhancer(L1/L2/L3 + 可选 LLM) | ✅ |
| [app/rag/bm25_fallback.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/bm25_fallback.py) | tokenize / BM25Index / BM25Fallback | ✅ |
| [app/rag/reranker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/reranker.py) | RerankService / BgeRerankerService / HeuristicReranker | ✅ |
| [app/rag/retriever.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/retriever.py) | Retriever(Dense + Sparse + 多模态 + RRF) | ✅ |
| [app/rag/service.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/service.py) | RAGServiceImpl 编排器 | ✅ |
| [app/rag/__init__.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/__init__.py) | 公开 API 导出 | ✅ |
| [app/config/settings.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L173-L212) | rag_* / bge_reranker_* / agnes_* / vector_index_* 配置项 | ✅ |
| [tests/unit/rag/test_protocols.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/test_protocols.py) | 协议契约 + 错误码 + Pydantic 校验 | ✅ |
| [tests/unit/rag/test_query_enhancer.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/test_query_enhancer.py) | L1/L2/L3 + UDT 器官检测 + LLM 回退 | ✅ |
| [tests/unit/rag/test_llm_enhancer.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/test_llm_enhancer.py) | AgnesLLMEnhancer 凭证 / HTTP / 解析 / health | ✅ |
| [tests/unit/rag/test_reranker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/test_reranker.py) | BgeRerankerService + HeuristicReranker + tokenize | ✅ |
| [tests/unit/rag/test_bm25_fallback.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/test_bm25_fallback.py) | BM25Index + BM25Fallback + tokenize | ✅ |
| [tests/integration/rag/test_rag_e2e.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/integration/rag/test_rag_e2e.py) | 端到端 100 query(召回率/准确率/P99 mock 验证) | ✅ |

### ❌ 关键阻塞（必须修复）
1. **app/embedding/protocols.py 缺失 6 个类型定义**
   - 缺失类: `SearchResult`, `VectorIndexClient`(ABC), `VectorIndexError`, `VectorIndexErrorCode`, `VectorIndexBackend`, `VectorRecord`
   - 影响: `qdrant_client.py` / `pgvector_client.py` / `index_factory.py` / `retriever.py` / `test_retriever.py` / `test_service.py` / `test_vector_index.py` 全部无法 import
   - **测试结果**: 2 collection errors,test_retriever.py 和 test_service.py 全部跳过
2. **requirements.txt 缺 rank-bm25 依赖**
   - `bm25_fallback.py` 直接 `from rank_bm25 import BM25Okapi`,未在 requirements 中声明
3. **app/main.py lifespan 未集成 RAG 服务**
   - 实际 lifespan (line 32-113) 只有 inference / speech / safety_gateway / observability,**没有 RAG 初始化或 close 逻辑**
   - 验收要求第 11 步明确指出: `app.state.rag_service = RAGServiceImpl()` 必须在 lifespan 中启动

### 已知未触及项
- `app/embedding/index_factory.py` / `qdrant_client.py` / `pgvector_client.py` 已存在但状态未审计;其依赖缺失的类型必须补全
- `app/embedding/bge_m3.py` / `ultrasound_clip.py` / `base.py` 是 RAG 依赖但已存在(参考 `tests/unit/emb/test_embedding.py`)

---

## 2. 实施步骤 (Proposed Changes)

### Step 1 — 修复关键阻塞:补全 `app/embedding/protocols.py`

**Why**: 这是测试无法运行的根因。`tests/unit/emb/test_vector_index.py` 已经写好了完整契约测试,可以从该文件反推需要补全的 6 个类的精确签名。

**How** — 在 `app/embedding/protocols.py` 末尾追加以下类（推断自 `test_vector_index.py` line 17-100 + 现有 `qdrant_client.py` / `pgvector_client.py` / `index_factory.py` 的实际使用）:

```python
from abc import ABC, abstractmethod
from typing import Any

class VectorIndexBackend(str, Enum):
    QDRANT = "qdrant"
    PGVECTOR = "pgvector"

class VectorIndexErrorCode(str, Enum):
    CONNECTION_FAILED = "E2003_VEC_CONNECTION_FAILED"
    UPSERT_FAILED = "E2003_VEC_UPSERT_FAILED"
    SEARCH_FAILED = "E2003_VEC_SEARCH_FAILED"
    DELETE_FAILED = "E2003_VEC_DELETE_FAILED"
    COLLECTION_NOT_FOUND = "E2003_VEC_COLLECTION_NOT_FOUND"
    DIMENSION_MISMATCH = "E2003_VEC_DIMENSION_MISMATCH"
    INVALID_INPUT = "E2003_VEC_INVALID_INPUT"

class VectorIndexError(Exception):
    def __init__(self, error_code, message, detail=None, retryable=False, retry_strategy="no_retry"): ...

class VectorRecord(BaseModel):
    id: str
    vector: list[float]
    metadata: dict[str, Any] = {}
    # 校验: id 非空, vector 非空

class SearchResult(BaseModel):
    id: str
    score: float
    metadata: dict[str, Any] = {}

class VectorIndexClient(ABC):
    @abstractmethod
    async def create_collection(self, collection, dimension, distance="cosine"): ...
    @abstractmethod
    async def upsert(self, collection, vectors): ...
    @abstractmethod
    async def search(self, collection, query_vector, top_k=10, filter=None): ...
    @abstractmethod
    async def delete_collection(self, collection): ...
    @abstractmethod
    async def health_check(self) -> bool: ...
    @abstractmethod
    async def close(self) -> None: ...
```

**Verification**: `pytest tests/unit/emb/test_vector_index.py -v` 全过(8 个类契约测试)。

### Step 2 — 添加 `rank-bm25` 依赖到 `requirements.txt`

**Why**: `bm25_fallback.py` line 27 `from rank_bm25 import BM25Okapi` 是 hard import,未在 requirements 中声明会让任何不预装该库的环境直接崩。

**How**:
```bash
echo "rank-bm25==0.2.2" >> requirements.txt
```

**Verification**: `pip show rank-bm25` 返回版本号;`python -c "from rank_bm25 import BM25Okapi"` 无 ImportError。

### Step 3 — 在 `app/main.py` lifespan 中集成 RAG 服务

**Why**: 验收要求第 11 步: `app.state.rag_service = RAGServiceImpl()` 必须在 lifespan 中启动 + `health_check()` 返回 True。

**How** — 在 [app/main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py) 的 `lifespan` 函数内(`await safety_gateway_client.warmup()` 之后,`yield` 之前)添加:

```python
# RAG service (T-GA-RAG-01/02/03)
rag_service = None
if settings.rag_use_query_enhancement or settings.rag_use_reranking:
    try:
        from app.embedding.index_factory import create_index_client_from_settings
        from app.embedding.bge_m3 import BgeM3MedicalService
        from app.embedding.ultrasound_clip import UltrasoundClipService
        from app.knowledge.udt_parser import UDTParser
        from app.knowledge.vocabulary import UDAFVocabulary
        from app.rag.bm25_fallback import BM25Fallback, BM25Index
        from app.rag.llm_enhancer import AgnesLLMEnhancer
        from app.rag.query_enhancer import QueryEnhancer
        from app.rag.reranker import BgeRerankerService, HeuristicReranker
        from app.rag.retriever import Retriever
        from app.rag.service import RAGServiceImpl

        # 仅当显式启用时构建
        rag_service = RAGServiceImpl(
            query_enhancer=QueryEnhancer(
                vocabulary=UDAFVocabulary(),
                udt_parser=UDTParser(),
                llm_enhancer=AgnesLLMEnhancer(
                    api_key=settings.agnes_api_key,
                    api_base=settings.agnes_api_base,
                    model=settings.agnes_api_model,
                ) if settings.rag_use_llm_enhancement and settings.agnes_api_key else None,
            ),
            retriever=Retriever(
                vector_client=await create_index_client_from_settings(),
                embedding_service=BgeM3MedicalService(
                    host=settings.bge_m3_host,
                    port=settings.bge_m3_port,
                ),
                ultrasound_clip=UltrasoundClipService(
                    host=settings.ultrasound_clip_host,
                    port=settings.ultrasound_clip_port,
                ),
            ),
            reranker=BgeRerankerService(
                base_url=f"http://{settings.bge_reranker_host}:{settings.bge_reranker_port}",
                model=settings.bge_reranker_model,
                timeout_seconds=settings.bge_reranker_timeout_seconds,
            ),
            heuristic_reranker=HeuristicReranker(),
            bm25_fallback=BM25Fallback(BM25Index(session_factory=None)),
        )
        # Health check (best effort, log on failure)
        try:
            healthy = await rag_service.health_check()
            logger.info("rag_service_health_check", healthy=healthy)
        except Exception as e:
            logger.warning("rag_service_health_check_failed", error=str(e))
        app.state.rag_service = rag_service
    except Exception as e:
        logger.warning("rag_service_init_failed", error=str(e))
        app.state.rag_service = None
```

在 `yield` 之后的关闭块添加:
```python
if hasattr(app.state, "rag_service") and app.state.rag_service:
    try:
        await app.state.rag_service.close()
    except Exception as e:
        logger.warning("rag_service_close_failed", error=str(e))
```

**Verification**: `python -c "from app.main import app; assert hasattr(app.state, 'rag_service')"` 导入无异常(实例属性不检查,仅类型导入)。

### Step 4 — 运行 RAG 单元测试,逐个修复失败

**Why**: TDD 原则 + 验收要求覆盖率 ≥ 90%。

**How**:
```bash
cd "e:\Ultrasound Diagnosis Decision Support AI System"
python -m pytest tests/unit/rag -v --tb=short 2>&1 | tee rag_test_run1.log
```

**期望结果**: 267 个 test item 全部 PASS(含 test_protocols / test_query_enhancer / test_llm_enhancer / test_retriever / test_reranker / test_bm25_fallback / test_service / test_knowledge)。

**失败修复策略**:
- 优先看 import error → 重复 Step 1
- AttributeError / TypeError → 检查 mock 是否 spec 与被 mock 对象一致
- 异步测试失败 → 确认 `pyproject.toml` 的 `asyncio_mode = "auto"` 已开启

**Verification**: 全部 PASS。

### Step 5 — 运行集成测试

**Why**: 验证编排器+查询增强+检索+精排+BM25 兜底端到端可工作。

**How**:
```bash
python -m pytest tests/integration/rag -v --tb=short 2>&1 | tee rag_e2e.log
```

**期望**: test_rag_e2e 全部 PASS(已 mock 所有外部依赖)。

### Step 6 — 创建新分支 + 切换

**Why**: 避免污染 DATA-01 已有的 commit;符合项目命名约定。

**How**:
```bash
cd "e:\Ultrasound Diagnosis Decision Support AI System"
git checkout -b feature/T-GA-RAG-01-02-03-rag-pipeline
git status  # 确认所有修改都在此分支
```

**Verification**: `git branch --show-current` 返回 `feature/T-GA-RAG-01-02-03-rag-pipeline`。

### Step 7 — Surgical Commit

**Why**: 工作区有其他轨道(EMB)的未提交修改,只 staged RAG 相关文件。

**需要 staged 的 RAG 相关文件**:
```
app/rag/__init__.py
app/rag/protocols.py
app/rag/query_enhancer.py
app/rag/retriever.py
app/rag/reranker.py
app/rag/bm25_fallback.py
app/rag/llm_enhancer.py
app/rag/service.py
tests/unit/rag/__init__.py
tests/unit/rag/test_protocols.py
tests/unit/rag/test_query_enhancer.py
tests/unit/rag/test_llm_enhancer.py
tests/unit/rag/test_retriever.py
tests/unit/rag/test_reranker.py
tests/unit/rag/test_bm25_fallback.py
tests/unit/rag/test_service.py
tests/unit/rag/test_knowledge.py
tests/integration/rag/__init__.py
tests/integration/rag/test_rag_e2e.py
```

**需要选择性 staged（与 EMB / DATA 共享文件,只选 RAG 相关 hunks）**:
- `app/embedding/protocols.py` — **整文件**(Step 1 新增 6 个类,但原 EMB 轨道也用;如果 EMB 已有 commit 则需协调;本会话先整文件提交,后续如有冲突再处理)
- `app/main.py` — 整文件(Step 3 新增 RAG 集成)
- `app/config/settings.py` — 整文件(RAG 配置项)
- `requirements.txt` — 整文件(Step 2 新增 rank-bm25)
- `.trae/documents/T-GA-RAG-01-02-03-rag-pipeline.md` — 原始计划文档
- `.trae/documents/T-GA-RAG-01-02-03-finalize-tests-and-archive.md` — 收尾计划文档

**操作**:
```bash
git add app/rag/ tests/unit/rag/ tests/integration/rag/
git add app/embedding/protocols.py app/main.py app/config/settings.py requirements.txt
git add .trae/documents/T-GA-RAG-01-02-03-*.md

# 验证
git status
git diff --cached --stat

git commit -m "feat(RAG): T-GA-RAG-01/02/03 - 完整RAG管线 (enhance+retrieval+rerank+BM25 fallback)"
```

**验证**: `git show --stat HEAD` 显示只含 RAG 相关文件; commit message 含 `T-GA-RAG-01/02/03`。

### Step 8 — 写归档报告

**文件**: `archive_of_achivement/archive_phase0A_rag_20260629/PROJECT_ARCHIVE.md`（新建）

**结构**（参考 [archive_of_achivement/archive_phase0A_emb_20260629/PROJECT_ARCHIVE.md](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/archive_of_achivement/archive_phase0A_emb_20260629/PROJECT_ARCHIVE.md)）:

1. **一、项目概述** — Phase 0A / RAG 轨道 / T-GA-RAG-01/02/03
2. **二、阶段目标** — 查询增强 + 多模态并行检索 + 精排 + BM25 兜底
3. **三、实现功能** — 7 个子模块(protocols / query_enhancer / retriever / reranker / bm25_fallback / llm_enhancer / service) + lifespan 集成
4. **四、关键设计决策**
   - UDAF + 可选 LLM(不强制 LLM,支持 Agnes API key 透传)
   - RRF 融合(k=60)而非线性加权
   - BM25 复用同一索引(retriever sparse leg + fallback 共享)
   - bge-reranker HTTP 客户端(避免主进程加载 torch)
   - HeuristicReranker 启发式降级(Jaccard + cosine)
   - Agnes LLM 失败回退 None
   - 缺失协议类型补全(6 个类):维持 EMB-02 向后兼容
5. **五、测试与验证** — 单元测试数、覆盖率、集成测试范围、已知 P99 限制
6. **六、文件清单** — 新增/修改的精确路径(含 18 个 RAG 文件 + 3 个共享文件)
7. **七、下游对接** — T-GA-AGT-03 知识查询智能体(依赖 RAG-02 / RAG-03)
8. **八、已知限制与后续** — 无 GPU 验证召回率指标、Qdrant 容器依赖、真实 bge-reranker 部署
9. **九、配置项** — 列出所有 `rag_*` / `bge_reranker_*` / `agnes_*` / `vector_index_*` 新增 settings

**Verification**: 文件存在,字数 > 1000,9 个章节齐全。

### Step 9 — 暂不 push(用户已确认)

按用户决策,本会话**不**执行 `git push`;commit 仅保留在本地 `feature/T-GA-RAG-01-02-03-rag-pipeline` 分支。

---

## 3. 关键决策记录 (Assumptions & Decisions)

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 是否补全缺失的 6 个向量索引协议类 | **是**(在 `app/embedding/protocols.py` 追加) | 阻塞测试运行;测试契约已写好,补全成本极低(< 50 行) |
| `protocols.py` 是整文件 staged 还是仅补丁 | 整文件 staged | 新增类追加到文件末尾,不修改现有类;git diff 干净 |
| 集成测试是否需真实 Qdrant / BgeM3 | **不**,全 mock | 沙箱无 Docker / 无 GPU;沿用 EMB 集成的 skip-by-default 风格 |
| `Retriever._safe_image` 的 collection_not_found 检测 | 已有正则匹配(retriever.py line 50-55) | 已实现并被测试覆盖 |
| 是否补 `EmbeddingService.embed_text_sparse` | **不**,plan 外任务 | 当前只用 `embed_text` + `embed_image`;sparse 由 BM25 独立索引负责 |
| main.py lifespan 集成策略 | 完整 RAG 装配 + best-effort health_check + try/except 关闭 | 遵循"启动失败不阻塞服务"的现有模式(speech / consent_consumer 同款) |
| 是否新建分支 | **是**, `feature/T-GA-RAG-01-02-03-rag-pipeline` | 用户已确认;避免污染 DATA-01 提交 |
| Commit 范围 | 18 个 RAG 专属文件 + 3 个共享文件(整文件) + 2 个 plan 文档 | 与 T-GA-DATA-01 surgical commit 一致 |
| 归档目录日期 | `archive_phase0A_rag_20260629` | 今日 2026-06-29 |
| 是否 push | **不** push | 用户已确认;沙箱 LFS quota 风险高 |
| Agnes LLM 集成 | 默认 **关闭**(`rag_use_llm_enhancement = False`) | 避免无 key 时报错;key 存在时自动启用 |

---

## 4. 验证清单 (Verification Steps)

执行完毕后,**逐项勾选**:

- [ ] 1. `pytest tests/unit/emb/test_vector_index.py -v` 全过(8 个 vector_index 契约测试)
- [ ] 2. `pytest tests/unit/rag -v` 全过(267+ test items,无 import 错误)
- [ ] 3. `pytest tests/integration/rag -v` 全过(test_rag_e2e)
- [ ] 4. `python -c "from app.rag.service import RAGServiceImpl"` 导入成功
- [ ] 5. `python -c "from app.rag.protocols import RAGService, RetrievalRequest, RetrievalResult"` 导入成功
- [ ] 6. 当前在 `feature/T-GA-RAG-01-02-03-rag-pipeline` 分支
- [ ] 7. `git log --oneline -1` 显示 `feat(RAG): T-GA-RAG-01/02/03 - 完整RAG管线`
- [ ] 8. `git show --stat HEAD` 只含 RAG 相关文件
- [ ] 9. `archive_of_achivement/archive_phase0A_rag_20260629/PROJECT_ARCHIVE.md` 已创建
- [ ] 10. `app/main.py` lifespan 包含 `app.state.rag_service = rag_service`
- [ ] 11. `app/config/settings.py` 包含 `rag_*` / `bge_reranker_*` / `agnes_*` 配置项
- [ ] 12. `requirements.txt` 包含 `rank-bm25==0.2.2`

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 补全 protocols.py 可能与 EMB 轨道的同文件修改冲突 | 先 stash 当前修改,逐步 add -p 选择 hunks;若 EMB 已 commit 则走 `git add -p` |
| 测试通过率受 `asyncio_mode` 配置影响 | 项目内 `asyncio_mode = "auto"` 已配置,无需手动装饰 |
| BM25 索引在 DB 不可用时启动失败 | main.py 集成用 try/except 包裹,失败时 `app.state.rag_service = None` + WARN log |
| bge-m3 / bge-reranker 服务在沙箱内不可用 | main.py 集成用 best-effort;单元测试全 mock,不受影响 |
| Qdrant 容器未启动 | `vector_index_auto_fallback=True` 触发 PGVector 降级,或在测试中 mock |
| integration test P99 测量无意义(全 mock) | 报告中显式声明"集成测试使用 mock 组件,P99 仅反映编排器开销,真实 P99 需 GPU 服务部署后实测" |
| rank-bm25 pip install 失败 | 沙箱预装则无需安装;若失败则跳过(单元测试用 mock 路径,bm25._build_locked 需真实 DB 才执行) |
| git 工作区脏状态导致 commit 污染 | 仅 `git add` RAG 相关路径;`git status` 二次确认 |
| 已有 plan 文件 `T-GA-RAG-01-02-03-finalize-tests-and-archive.md` 与本计划重复 | 本计划以"修复 + 收尾"为主,继承原计划的步骤细节但补充了缺失的 3 个关键阻塞 |

---

## 6. 执行顺序 (Execution Order)

按依赖关系,严格顺序执行:

```
Step 1 (protocols.py 补全)
    ↓
Step 2 (requirements.txt)
    ↓
Step 3 (main.py 集成)
    ↓
Step 4 (跑单元测试 + 修复)
    ↓
Step 5 (跑集成测试)
    ↓
Step 6 (新建分支)
    ↓
Step 7 (surgical commit)
    ↓
Step 8 (写归档报告)
    ↓
Step 9 (完成, 不 push)
```

预计工作量:**约 2-3 小时**(主体代码已写好,主要是修复 + 测试验证 + 归档)。

---

## 7. 关键文件路径速查

| 类别 | 路径 |
|------|------|
| RAG 核心代码 | [app/rag/](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/) |
| RAG 单元测试 | [tests/unit/rag/](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/) |
| RAG 集成测试 | [tests/integration/rag/](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/integration/rag/) |
| 待补全的协议文件 | [app/embedding/protocols.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/embedding/protocols.py) |
| 待修改的 lifespan | [app/main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py) |
| 配置项 | [app/config/settings.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L173-L212) |
| 依赖 | [requirements.txt](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/requirements.txt) |
| 归档目录 | `archive_of_achivement/archive_phase0A_rag_20260629/`(新建) |
| 现有参考归档 | [archive_of_achivement/archive_phase0A_emb_20260629/PROJECT_ARCHIVE.md](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/archive_of_achivement/archive_phase0A_emb_20260629/PROJECT_ARCHIVE.md) |
