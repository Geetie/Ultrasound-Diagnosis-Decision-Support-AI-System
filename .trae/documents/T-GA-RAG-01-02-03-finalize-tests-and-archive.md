# T-GA-RAG-01/02/03 — 收尾执行计划（测试 + 归档 + 提交）

> 轨道: **RAG** | Wave 2 收尾 | 阶段: 验证 → 提交 → 归档 | 今日: 2026-06-29
>
> 范围: 主体实现已完成（protocols / query_enhancer / retriever / reranker / bm25_fallback / llm_enhancer / service / lifespan wiring / settings 扩展），剩余 = **补全测试套件 + 写归档报告 + commit**。

---

## 1. 当前状态盘点 (Current State)

### ✅ 已实现（无需重写）
| 文件 | 用途 | 状态 |
|------|------|------|
| [app/rag/protocols.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/protocols.py) | `RAGErrorCode` / `RAGError` / `RetrievalMethod` / `RetrievalRequest` / `ScoredChunk` / `EnhancedQuery` / `RetrievalResult` / `RAGService` ABC | ✅ |
| [app/rag/llm_enhancer.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/llm_enhancer.py) | `LLMEnhancer` ABC + `AgnesLLMEnhancer`（httpx 客户端，失败回退 None） | ✅ |
| [app/rag/query_enhancer.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/query_enhancer.py) | `QueryEnhancer`（L1/L2/L3 + 可选 LLM，复用 UDT parser） | ✅ |
| [app/rag/bm25_fallback.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/bm25_fallback.py) | `tokenize` / `BM25Index`（懒加载 + kb_version 校验 + max 10000） + `BM25Fallback`（policy + search） | ✅ |
| [app/rag/reranker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/reranker.py) | `RerankService` ABC / `BgeRerankerService`（HTTP 客户端）/ `HeuristicReranker`（Jaccard+cosine）/ `RerankError` | ✅ |
| [app/rag/retriever.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/retriever.py) | `Retriever`（Dense + Sparse + 多模态并行 + RRF 融合 + 失败软降级） | ✅ |
| [app/rag/service.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/service.py) | `RAGServiceImpl` 编排器（enhance → retrieve → rerank → bm25_fallback + P99 埋点） | ✅ |
| [app/rag/__init__.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/rag/__init__.py) | 公开 API 导出 | ✅ |
| [app/main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py#L58-L160) | lifespan 注入 `app.state.rag_service` + 启动时 `health_check()` | ✅ |
| [app/config/settings.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L173-L212) | `vector_index_backend` / `qdrant_*` / `rag_*` / `bge_reranker_*` / `agnes_*` 配置项 + 端口冲突校验 | ✅ |
| [requirements.txt](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/requirements.txt) | `rank-bm25==0.2.2` | ✅（需确认） |
| [tests/unit/rag/test_protocols.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/test_protocols.py) | 协议契约 + 枚举 + 错误码 + Pydantic 校验 | ✅ |
| [tests/unit/rag/test_query_enhancer.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/rag/test_query_enhancer.py) | L1/L2/L3 扩展 + UDT 器官检测 + LLM 回退 + max_per_layer cap | ✅ |

### ❌ 待完成（本次执行）
1. **单元测试补全**：test_llm_enhancer / test_retriever / test_reranker / test_bm25_fallback / test_service
2. **集成测试**：test_rag_e2e（端到端召回率/准确率/P99 验证，可 mock）
3. **运行测试 + 修复**：确保全部通过
4. **写归档报告**：`archive_of_achivement/archive_phase0A_rag_20260629/PROJECT_ARCHIVE.md`
5. **Surgical commit**：仅 RAG 轨道文件 + 相关测试（沿用 T-GA-DATA-01 的 surgical 模式）

---

## 2. 实施步骤 (Proposed Changes)

### Step 1 — 编写单元测试（5 个文件）

**Why**: T-GA-RAG 验收要求「单元测试覆盖率 ≥ 90%」。当前已覆盖 protocols + query_enhancer，还差 4 个核心模块 + 编排器。

#### 1.1 `tests/unit/rag/test_llm_enhancer.py`

覆盖：
- `AgnesLLMEnhancer._has_credentials()`：空 / 全空白 / 正常 key
- `enhance()`：缺 key → 立即返回 None（不调 HTTP）
- `enhance()`：HTTP 200 + `rewritten_query` 字段 → 返回字符串
- `enhance()`：HTTP 200 + `choices[0].message.content` JSON 字符串 → 解析
- `enhance()`：HTTP 200 + `choices[0].message.content` 非 JSON → 原样返回
- `enhance()`：HTTP 200 + 缺 `rewritten_query` → None
- `enhance()`：HTTP 4xx/5xx → None（不抛）
- `enhance()`：`httpx.TimeoutException` → None
- `enhance()`：`httpx.ConnectError` → None
- `enhance()`：非 200 响应 body 被截断到 200 字符（log 字段）
- `health_check()`：缺 key → False
- `health_check()`：200 → True
- `close()`：第二次 close 不抛
- 复用 `httpx.MockTransport` 或 `respx` 模拟 HTTP 响应（**复用 project 已有模式**）

#### 1.2 `tests/unit/rag/test_retriever.py`

构造最小 fixture：
- 假的 `VectorIndexClient`（继承 `VectorIndexClient` ABC 或 `MagicMock(spec=VectorIndexClient)`）
- 假的 `EmbeddingService`（同上）
- 假的 `BM25Index`（直接构造，绕过 DB）

覆盖：
- **Dense-only 路径**：mock `vector_client.search` 返回固定 `SearchResult` 列表
- **Sparse-only 路径**：`vector_client` 抛错但 BM25 有结果 → 仍能返回
- **Hybrid 路径**：Dense + Sparse 同时给结果 → RRF 融合分数正确（验证 `1/(k+rank)` 公式）
- **Hybrid 同一 chunk 出现在两路**：去重保留 RRF 分数高的
- **多模态路径**：`image_data` 不为空 + `_clip` 不为 None → 调 `embed_image` + image collection search
- **多模态 collection 缺失**：`VectorIndexError("collection not found")` → 软跳过
- **器官过滤**：`request.organ = "thyroid"` → `vector_client.search` 的 `filter` 参数含 `{"organ": "thyroid"}`
- **Dense 失败软降级**：`embedding.embed_text` 抛 `EmbeddingError` → 不抛异常，结果包含 sparse 命中
- **全部失败**：所有 leg 都抛错 → 返回空 `RetrievalResult`（`has_results == False`）
- **空 input**：`_safe_dense` / `_safe_sparse` / `_safe_image` 输入为 `[]` → 各路径返回 `[]`
- **结果按 top_k 截断**：`request.top_k=3` → 返回 ≤ 3 条
- **不使用 session_factory**：触发 placeholder 分支（无 DB）
- **RRF 公式校验**：人工算 fused score，与 `Retriever._rrf_fuse` 输出比较
- **不同 leg 的 retrieval_source 保留**：dense 命中的 chunk `retrieval_source == DENSE`

#### 1.3 `tests/unit/rag/test_reranker.py`

覆盖：
- `BgeRerankerService.rerank()`：200 + 合法 `results` → 排序后返回 top_k
- `BgeRerankerService.rerank()`：HTTP 500 → `RerankError`
- `BgeRerankerService.rerank()`：`httpx.TimeoutException` → `RerankError`
- `BgeRerankerService.rerank()`：响应非 JSON → `RerankError`
- `BgeRerankerService.rerank()`：响应缺 `results` 字段 → `RerankError`
- `BgeRerankerService.rerank()`：空 chunks → 返回 `[]`
- `BgeRerankerService.health_check()`：200 → True；其他 → False
- `BgeRerankerService.close()`：关闭后再次调用 → 抛 `RerankError("closed")`
- `HeuristicReranker.rerank()`：相同 query 多次调 → 分数稳定
- `HeuristicReranker.rerank()`：高重叠 query → 分数高
- `HeuristicReranker.rerank()`：embedding 为 None → cosine 退化为 0
- `HeuristicReranker.rerank()`：`jaccard_weight + cosine_weight ≠ 1.0` → 构造抛 `ValueError`
- `HeuristicReranker.health_check()`：永远 True
- `tokenize()` 函数（来自 `bm25_fallback`）：中文 / 英文 / 混合 / 空 / 标点

#### 1.4 `tests/unit/rag/test_bm25_fallback.py`

构造 fixture：直接构造 `_BuiltIndex`（私有） 或用 monkeypatch 注入小语料。

覆盖：
- `tokenize()`：中文 / 英文 / 大小写 / 标点
- `BM25Index.ensure_built()`：首次调用 → 构建；第二次调用 → 跳过（cached）
- `BM25Index.ensure_built()`：`kb_version` 变化 → 重建
- `BM25Index.search()`：未构建 → 返回 `[]` + warn log
- `BM25Index.search()`：空查询 → 返回 `[]`
- `BM25Index.search()`：`organ_filter` 生效
- `BM25Index.search()`：BM25 分数为负 → 截断到 0
- `BM25Index.health_check()`：未构建 → False；构建后 → True
- `BM25Index.is_built` / `num_chunks` / `close` 基本属性
- `BM25Index._build_locked`：`max_chunks` 上限触发 WARN
- `BM25Fallback.should_trigger()`：
  - 空 results → True
  - top1 < 阈值 → True
  - chunks 数 < `min_results` → True
  - 否则 → False
- `BM25Fallback.trigger_reason()`：返回对应 reason 字符串
- `BM25Fallback.fallback_search()`：未构建 → `[]`；已构建 → `ScoredChunk` 列表带 `BM25_FALLBACK` source

#### 1.5 `tests/unit/rag/test_service.py`

构造 fixture：mock 全部依赖（QueryEnhancer / Retriever / BgeReranker / HeuristicReranker / BM25Fallback）。

覆盖：
- **Happy path**：enhance → retrieve (top-5) → rerank → 不触发 fallback
- **Reranker 失败降级**：`bge_reranker.rerank` 抛 `RerankError` → `heuristic_reranker` 被调用
- **Reranker 未知异常降级**：抛 `RuntimeError` → heuristic
- **BM25 兜底触发**：`bm25_fallback.should_trigger` 返回 True → `fallback_search` 被调 + `result.retrieval_method == BM25_FALLBACK`
- **BM25 兜底无结果**：`fallback_search` 返回 `[]` → 保持原 retriever 结果
- **Query enhancement 失败降级**：`enhancer.enhance` 抛异常 → 走原始 query
- **min_score_threshold 过滤**：最终结果分数 ≥ 阈值
- **P99 violation**：`p99_critical_ms=0` 强制所有请求触发 → `result.p99_violation == True`
- **P99 warning**：延迟在 warning 与 critical 之间 → 触发 info log
- **enhanced_query 字段**：`use_query_enhancement=True` 时填入 `EnhancedQuery.enhanced`
- **llm_enhanced 透传**：`EnhancedQuery.llm_enhanced` → `RetrievalResult.llm_enhanced`
- **rerank_used 正确性**：通过 mock 计数验证（无需暴露内部字段，验证 reranker 行为）
- **`use_reranking=False`**：跳过 rerank 步骤，heuristic 不被调用
- **`use_bm25_fallback=False`**：跳过 fallback
- **latency_ms ≥ 0** 始终成立
- **`health_check()` 全成功 / 全失败 / 部分失败**：
  - 全成功 → True
  - 任何一个子检查抛 → 继续检查其他；最终 `False`（不抛）
- **`close()`**：所有依赖的 `close()` 被调用
- **`close()` 部分失败**：某个子 `close()` 抛 → 继续其他
- **`retriever` 抛异常**：服务抛 `RAGError(RETRIEVAL_FAILED)`

### Step 2 — 编写集成测试 `tests/integration/rag/test_rag_e2e.py`

> 本环境无 Qdrant / BgeM3 / Agnes / BgeReranker，集成测试必须 **mock 所有外部依赖**，但保留完整的"enhance → retrieve → rerank → bm25_fallback"管线。

**测试模式**（参考 [tests/integration/emb/test_qdrant_integration.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/integration/emb/test_qdrant_integration.py) 的"环境缺失 skip"风格，但 mock 实际依赖）：

```python
pytestmark = [
    pytest.mark.integration,
    pytest.mark.track_rag,
    pytest.mark.asyncio,  # asyncio_mode=auto 时可选
]
```

覆盖：
- **端到端 100 条 query**（用随机生成 + ground truth fixture）：
  - 调用完整管线
  - 验证 召回率 ≥ 90%（mock retriever 返回固定 ground truth，验证 RAGService 返回的 top-k 包含 ground truth）
- **扩展后召回率提升**：同一组 query，对比 `use_query_enhancement=True` vs `False` 的召回率；提升 ≥ 10%
- **精排后 Top-5 准确率**：mock reranker 返回 ground-truth-aware 分数，验证 top-5 命中 ground truth ≥ 85%
- **P99 延迟**：100 条 query，验证 `p99(latency_ms) < 100ms`（mock 全部子组件零耗时，主流程 < 5ms）

### Step 3 — 运行测试 + 修复

**Why**: TDD 原则 — 实现完成后必须 verify tests pass。

**动作**:
```bash
cd "e:\Ultrasound Diagnosis Decision Support AI System"
python -m pytest tests/unit/rag -v -p no:xdist
python -m pytest tests/integration/rag -v -p no:xdist
```

**验证标准**:
- 所有 unit test PASS
- 所有 integration test PASS
- 无 import 错误

**失败处理**:
- 如有失败，按 TDD 原则修复实现（**不**改测试以适配坏实现）
- 常见原因：mock 漏掉子组件 / 异步 fixture 缺 `@pytest_asyncio.fixture` / `asyncio_mode` 不匹配

### Step 4 — Surgical Commit

**Why**: 工作区有其他轨道（DATA / LLM / API / SEC / SG / FE / EMB）的未提交修改，**必须只 staged RAG 相关文件**。

**需要新增的 RAG 相关文件**:
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
tests/unit/rag/test_llm_enhancer.py         # 新增
tests/unit/rag/test_retriever.py             # 新增
tests/unit/rag/test_reranker.py              # 新增
tests/unit/rag/test_bm25_fallback.py         # 新增
tests/unit/rag/test_service.py               # 新增
tests/unit/rag/test_knowledge.py             # 已存在（vocab / parser 回归）
tests/integration/rag/__init__.py            # 新增
tests/integration/rag/test_rag_e2e.py        # 新增
```

**需要选择性 staged（与 DATA / EMB 共享）**:
- `app/config/settings.py` — 只选 RAG 相关 hunks（line 173-212）
- `app/main.py` — 只选 lifespan 中 RAG 相关 hunks（line 58-160）
- `requirements.txt` — 只选 `rank-bm25==0.2.2` 行

**操作流程**:
1. `git status` 确认待提交文件范围
2. `git diff app/config/settings.py` / `git diff app/main.py` / `git diff requirements.txt` 检查 hunks 是否冲突
3. 若无冲突 → `git add` 整文件
4. 若有冲突 → `git add -p` 选择性 staged
5. 提交：
   ```bash
   git commit -m "feat(RAG): T-GA-RAG-01/02/03 - 完整RAG管线 (enhance+retrieval+rerank+BM25 fallback)"
   ```
6. **不要** `git add .` 或 `git add -A`！

**验证**: `git show --stat HEAD` 确认只含 RAG 相关文件。

### Step 5 — 写归档报告

**文件**: `archive_of_achivement/archive_phase0A_rag_20260629/PROJECT_ARCHIVE.md`（新建）

**结构**（参考 [archive_of_achivement/archive_phase0A_emb_20260629/PROJECT_ARCHIVE.md](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/archive_of_achivement/archive_phase0A_emb_20260629/PROJECT_ARCHIVE.md)）:

1. **一、项目概述** — Phase 0A / RAG 轨道 / T-GA-RAG-01/02/03
2. **二、阶段目标** — 查询增强 + 多模态并行检索 + 精排 + BM25 兜底
3. **三、实现功能** — 7 个子模块（protocols / query_enhancer / retriever / reranker / bm25_fallback / llm_enhancer / service）
4. **四、关键设计决策**
   - UDAF + 可选 LLM（不强制 LLM）
   - RRF 融合（k=60）而非线性加权
   - BM25 复用同一索引（retriever sparse leg + fallback 共享）
   - bge-reranker HTTP 客户端（避免主进程加载 torch）
   - HeuristicReranker 启发式降级（Jaccard + cosine）
   - Agnes LLM 失败回退 None
5. **五、测试与验证** — 单元测试数、覆盖率（目标 ≥ 90%）、集成测试范围
6. **六、文件清单** — 新增/修改的精确路径
7. **七、下游对接** — T-GA-AGT-03 知识查询智能体（依赖 RAG-02 / RAG-03）
8. **八、已知限制与后续** — 无 GPU 验证召回率指标、Qdrant 容器依赖、真实 bge-reranker 部署
9. **九、配置项** — 列出所有 `rag_*` / `bge_reranker_*` / `agnes_*` 新增 settings

### Step 6 — 通知下游

- **T-GA-AGT-03 知识查询智能体**（依赖 RAG-02 / RAG-03）：
  - API 入口：`from app.rag.service import RAGServiceImpl`
  - 调用方式：`await rag_service.retrieve(RetrievalRequest(...))`
  - 配置：`app.state.rag_service` 已在 lifespan 中就绪

---

## 3. 关键决策记录 (Assumptions & Decisions)

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 测试 mocking 策略 | 用 `MagicMock(spec=...)` + `AsyncMock` + `httpx.MockTransport` | 项目内已有模式（test_vector_index.py / test_query_enhancer.py），一致性好 |
| 集成测试是否需真实 Qdrant | **不**，全 mock | 沙箱无 Docker / 无 GPU；沿用 EMB 集成的 skip-by-default 风格 |
| `Retriever._safe_image` 的 collection_not_found 检测 | 已实现正则匹配（line 50-55），单元测试覆盖 | retriever 已实现 |
| 是否补 `EmbeddingService.embed_text_sparse` | **不**，plan 外任务 | 当前只用 `embed_text` + `embed_image`；sparse 由 BM25 独立索引负责 |
| `tests/integration/rag/__init__.py` 是否需要 | **是**（pytest collection 依赖） | 沿用其他集成包风格 |
| Commit 范围 | 仅 RAG 9 个 src + 8 个 test + 选择性 settings/main/requirements hunks | 与 T-GA-DATA-01 surgical commit 一致 |
| 分支名 | `feature/T-GA-RAG-01-02-03-rag-pipeline` | 任务规约命名约定（与 DATA 轨道 `feature/T-GA-DATA-01-kb-builder` 一致） |
| 归档目录日期 | `archive_phase0A_rag_20260629` | 今日 2026-06-29 |
| 是否 push | 尝试 push；失败时记录但不阻塞 | 沙箱可能无网络 |
| 集成测试文件位置 | `tests/integration/rag/test_rag_e2e.py` | 与 EMB / SG 集成测试同结构 |

---

## 4. 验证清单 (Verification Steps)

执行完毕后，**逐项勾选**：

- [ ] 1. `pytest tests/unit/rag -v` 全过（含 test_llm_enhancer / test_retriever / test_reranker / test_bm25_fallback / test_service）
- [ ] 2. `pytest tests/integration/rag -v` 全过（test_rag_e2e）
- [ ] 3. `from app.rag.service import RAGServiceImpl` 导入成功
- [ ] 4. `from app.rag.protocols import RAGService, RetrievalRequest, RetrievalResult` 导入成功
- [ ] 5. 当前在 `feature/T-GA-RAG-01-02-03-rag-pipeline` 分支
- [ ] 6. `git log --oneline -1` 显示 `feat(RAG): T-GA-RAG-01/02/03 - 完整RAG管线`
- [ ] 7. `git show --stat HEAD` 只含 RAG 相关文件
- [ ] 8. `archive_of_achivement/archive_phase0A_rag_20260629/PROJECT_ARCHIVE.md` 已创建
- [ ] 9. `app/main.py` lifespan 包含 `app.state.rag_service = rag_service`
- [ ] 10. `app/config/settings.py` 包含 `rag_*` / `bge_reranker_*` / `agnes_*` 配置项

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| `Retriever._wrap_search_results` 私有方法测试难度大 | 通过公开 `retrieve()` API 间接验证（mock `vector_client.search` 返回 → 验证最终 `RetrievalResult`） |
| `httpx.MockTransport` 在 `AsyncClient` 上的使用需要 `async` 上下文 | 复用 `tests/unit/emb/test_vector_index.py` 中已验证的模式（搜索是否已有 httpx mock 样例） |
| 工作区脏状态导致 commit 污染 | Surgical `git add -p` 选择 hunks，**不**用 `git add .` |
| asyncio_mode=auto 下 `@pytest_asyncio.fixture` 可选 | 项目内 `asyncio_mode = "auto"` 已配置（[pyproject.toml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/pyproject.toml)），无需手动装饰 |
| 集成测试 P99 测量无意义（mock 全快） | 在报告中显式声明"集成测试使用 mock 组件，P99 测量仅反映编排器开销，真实 P99 需在 bge-m3 / Qdrant 部署后实测" |
| `requirements.txt` 是否真有 `rank-bm25==0.2.2` | 提交前 `grep` 确认；缺失则补 `echo "rank-bm25==0.2.2" >> requirements.txt` |
| `app/config/settings.py` 的 `bge_reranker_port` 端口冲突校验是否在位 | 已在 line 384 看到 `'bge_reranker_port'`，OK |
