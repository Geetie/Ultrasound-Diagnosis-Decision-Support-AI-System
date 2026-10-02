# RAG / 知识库 / EMB 三管线系统性审查报告

**Tag:** `debug-pro-1.0.0`
**审查日期:** 2026-06-30
**审查范围:**
- `e:\Ultrasound Diagnosis Decision Support AI System\app\rag\*.py` (RAG 管线)
- `e:\Ultrasound Diagnosis Decision Support AI System\app\knowledge\*.py` (知识库管线)
- `e:\Ultrasound Diagnosis Decision Support AI System\app\embedding\*.py` (EMB 管线)
- `docs-for-guiding generating docs\超声诊疗决策支持系统\02-核心流程与知识库.md`
- `docs-for-guiding generating docs\超声诊疗决策支持系统\01-系统架构设计.md`
- `tests/integration/rag/test_debug_pro_real_scenarios.py`

---

## 1. 执行摘要

| 维度 | 结果 |
|---|---|
| 测试用例数 | 37 (debug-pro-1.0.0 全部真实临床场景) |
| 修复前通过 | 34/37 (3 失败) |
| **修复后通过** | **37/37 (100%)** |
| 涉及管线 | RAG / KB / EMB |
| RAG 全量回归 | 379/379 通过 (4 跳过) |
| EMB + KB 全量回归 | 314/314 通过 (10 跳过) |

### 1.1 修复前失败用例

| # | 测试 | 失败原因 | 偏离位置 |
|---|---|---|---|
| F1 | `test_bge_reranker_5xx_degrades_to_heuristic` | top1=heart 应为 thyroid | 测试未注入 `query_embed_fn`，与生产配置不一致 |
| F2 | `test_embedding_service_retry_on_transient_error` | 抽象类 `FlakyService` 未实现 `embed_text/embed_text_sparse` | 测试 mock 遗漏 |
| F3 | `test_amiodarone_thyroid_rule` | cross-002 (胺碘酮→甲功异常) 未进 top-3 | Mock 嵌入按"首匹配子串"分类器官，跨器官查询被错归为"甲状腺" |

### 1.2 修复要点

| 修复 | 涉及文件 | 类型 |
|---|---|---|
| 器官判定改为"特异标记词集合打分" | `test_debug_pro_real_scenarios.py::MockEmbeddingService` | mock 优化 |
| `HeuristicReranker` 注入 `query_embed_fn` 复现生产配置 | `test_debug_pro_real_scenarios.py::test_bge_reranker_5xx_degrades_to_heuristic` | 测试修正 |
| `FlakyService` 补全抽象方法 | 同上 | mock 补全 |
| `object.__setattr__` 绕过 Pydantic v2 严格 setattr | `test_embedding_service_retry_on_transient_error` | 测试修正 |

---

## 2. RAG 管线审查

### 2.1 设计 vs 实施映射

| 设计要求 (§2.1 / §2.3) | 实施模块 | 状态 | 验证测试 |
|---|---|---|---|
| 查询增强 (QueryEnhancer) | `app/rag/query_enhancer.py` | ✅ | `tests/unit/rag/test_query_enhancer.py` |
| 多模态检索 (Dense + Sparse + Image) | `app/rag/retriever.py` | ✅ | `TestEndToEndPipeline` |
| RRF 融合 (Cormack 2009, k=60) | `app/rag/retriever.py` (RRF) | ✅ | 同上 |
| bge-reranker 跨编码器 | `app/rag/reranker.py::BgeRerankerService` | ✅ | `TestRerankFallbackScenarios` |
| HeuristicReranker 降级 (Jaccard + Cosine) | `app/rag/reranker.py::HeuristicReranker` | ✅ | `test_heuristic_*` |
| BM25 终极兜底 (UDAFMaxMatchTokenizer) | `app/rag/bm25_fallback.py` | ✅ | `TestBM25FallbackScenarios` |
| 异常分支 `evidence_insufficient` | `app/rag/service.py` | ✅ | `TestDegradationScenarios` |
| P99 延迟监控 | `app/rag/service.py` (warning/critical log) | ✅ | `TestP99Latency` |
| 原始 query 用于 rerank（非 enhanced） | `app/rag/service.py:134` (修复点) | ✅ | `test_heuristic_rerank_uses_original_query_not_enhanced` |
| 检索结果保留 retrieval_source | `app/rag/service.py` | ✅ | `TestRetrievalSourceAfterRerank` |

### 2.2 偏离项

| # | 偏离 | 严重度 | 处置 |
|---|---|---|---|
| R-D1 | `HeuristicReranker._cosine` 早期实现使用 self-norm 而非 query-chunk pairwise similarity | P0（已修复） | 重命名为 `_cosine_pair`，按 pairwise cosine 计算 |
| R-D2 | `service.py` 早期版本向 rerank 传 `enhanced.enhanced` 而非 `request.query` | P0（已修复） | 改传 `request.query` |
| R-D3 | `app/main.py` BM25 索引初始化时 `session_factory=None` | P0（已修复） | 注入 `AsyncSessionLocal` + `UDAFVocabulary()` |
| R-D4 | 一期未实现 CRAG 补充检索的具体路由 | P2（二期） | 一期仅在 RAGService 中预留 trigger 接口 |

### 2.3 真实临床场景测试 (RAG 部分)

`test_debug_pro_real_scenarios.py::TestClinicalRetrievalScenarios` 覆盖:

| 临床场景 | 器官 | 验证点 | 状态 |
|---|---|---|---|
| TI-RADS 4b 微钙化 + 不规则 | thyroid | dense 检索 + 器官过滤 | ✅ |
| TI-RADS 3 囊性 + 透声好 | thyroid | 良性匹配 | ✅ |
| 桥本甲状腺炎 (弥漫性回声减低) | thyroid | 弥漫性病变路径 | ✅ |
| LI-RADS 5 (HCC 三联征) | liver | 跨器官规则触发 (肝→心) | ✅ |
| 单纯性肝囊肿 | liver | 良性匹配 | ✅ |
| 甲亢性心脏病 (心率偏快 + 左室壁弥漫性增强) | heart | 跨器官规则触发 (甲→心) | ✅ |
| 甲亢 + 肝功能异常 (ALT=120) | thyroid→liver | cross-001 命中 | ✅ |
| 心律失常 + 胺碘酮 + 甲状腺功能异常 | heart→thyroid | cross-002 命中 | ✅ (修复后) |
| 无器官过滤返回跨器官规则 | all | 跨器官关联可达 | ✅ |

---

## 3. 知识库管线审查

### 3.1 设计 vs 实施映射

| 设计要求 (§1 / §1.3) | 实施模块 | 状态 | 验证测试 |
|---|---|---|---|
| UDT 标准化对齐 (UHAT/UDAF) | `app/knowledge/udt_parser.py`, `vocabulary.py` | ✅ | `test_knowledge.py` |
| 患者身份脱敏 (B0 子步骤) | `app/knowledge/desensitization.py` | ✅ | `test_desensitization.py` |
| 术语一致性校验 (B1) | `app/knowledge/vocabulary.py` | ✅ | `test_knowledge.py` |
| 文本语义分块 (C) | `app/knowledge/chunker.py` | ✅ | `test_chunker` |
| 父子块结构生成 (D) | `KnowledgeChunk.chunk_type` + `parent_chunk_id` | ✅ | `TestParentChildChunkScenarios` |
| 多模态特征提取 (E) | `app/knowledge/feature_extractor.py` | ✅ | `test_feature_extractor` |
| 混合索引构建 (F) | `app/knowledge/hybrid_indexer.py` | ✅ | `test_hybrid_indexer` |
| 知识图谱构建 (G) | `app/knowledge/graph_query.py` | ⚠️ STUB | `test_graph_query_stub` |
| 版本化入库 (H) | `app/knowledge/version.py` | ✅ | `TestKBVersionScenarios` |
| KB 版本管理 2 层决策机制 | `app/knowledge/version_policy.py` | ✅ | `test_safety_critical_update_terminates_active_sessions` |
| `parse_kb_version` 多租户解析 | `app/knowledge/version.py` | ✅ | `test_parse_kb_version_with_hospital` |
| 兼容性矩阵 (v1.x↔kb_v1.x, v1.x↔kb_v2.x) | `is_compatible()` | ✅ | `test_kb_version_compatibility_v1_kb_v2_degraded` |
| 4 步缓存读取判定链 | `KBVersionPolicy.resolve_version()` | ✅ | `test_resolve_version_4step_priority` |
| L2/L3 反馈驱动衰减 | 设计文档说明 | ⚠️ P2 | 一期仅 spec，未实现 |
| Confidence × 0.8 / × 0.7 | 设计文档说明 | ⚠️ P2 | 一期仅 spec，未实现 |

### 3.2 偏离项

| # | 偏离 | 严重度 | 处置 |
|---|---|---|---|
| K-D1 | `graph_query.PgVectorGraphAdapter` 一期 STUB (P2-KB-08) | P2 | 接入条件 `settings.graph_query_enabled` 已明确 |
| K-D2 | L2/L3 反馈驱动衰减 (`§1.2`) 未实现 | P2 | 一期范围外，二期实现 |
| K-D3 | 知识条目置信度衰减 ×0.8/×0.7 (`§1.2`) 未实现 | P2 | 一期范围外 |
| K-D4 | 知识条目 `applicable_population` 字段（`§5.1`）未建模 | P2 | 待 Gate 0B 审定后加入 |
| K-D5 | LLM 幻觉 vs 知识错误的 `challenge_source` 复审区分 | P2 | 一期人工复审表，无自动判定 |

### 3.3 真实临床场景测试 (KB 部分)

`TestKBVersionScenarios` 覆盖:

| 场景 | 验证点 | 状态 |
|---|---|---|
| `kb_v1.2.3` 基础解析 | major/minor/patch 正确 | ✅ |
| `kb_v2.1.0~hospital_A` 多租户解析 | `~` 分隔符 + hospital_id 提取 | ✅ |
| `kb_v1.2.3~hospital_A_branch` 下划线兼容 | hospital_id 包含 `_` | ✅ |
| 非法格式 (1.2.3 / kb_v1-2-3 / 空) | 拒绝并返回 None | ✅ |
| 兼容性矩阵 v1.x+kb_v2.x → 降级兼容 | True | ✅ |
| 兼容性矩阵 v2.x+kb_v1.x → 不兼容 | False | ✅ |
| `KBVersion(10,0,0) > KBVersion(2,0,0)` | 元组比较避免字典序陷阱 | ✅ |
| safety_critical 更新 → 终止所有活跃会话 | E4006 行为 | ✅ |
| 非 safety_critical 更新 → 保留会话锁定 | 4 步链第 2 步 | ✅ |
| 4 步优先级: requested > safety_critical > session_lock > fallback | resolve_version() | ✅ |

---

## 4. EMB 管线审查

### 4.1 设计 vs 实施映射

| 设计要求 (§5.2) | 实施模块 | 状态 | 验证测试 |
|---|---|---|---|
| 主方案 Ultrasound-CLIP (768 维, 含 DTCWT) | `app/embedding/ultrasound_clip.py`, `clip_model.py` | ✅ | `test_clip_model`, `test_ultrasound_clip_sustained` |
| 强兜底 bge-m3-medical (1024 维) | `app/embedding/bge_m3.py` | ✅ | `test_embedding` |
| 终极兜底 BM25 (UDAF 词汇表) | `app/rag/bm25_fallback.py` (BM25Index) | ✅ | `TestBM25FallbackScenarios` |
| Qdrant 向量索引 | `app/embedding/qdrant_client.py` | ✅ | `test_qdrant_client`, `test_qdrant_integration` |
| PGVector 降级 | `app/embedding/pgvector_client.py` | ✅ | `test_pgvector_client`, `test_pgvector_integration` |
| Qdrant→PGVector 自动降级 | `app/embedding/index_factory.py` | ✅ | `test_qdrant_unavailable_auto_fallback_to_pgvector` |
| EmbeddingError + retryable 标识 | `app/embedding/protocols.py` | ✅ | `TestEmbeddingErrorCode` |
| 重试退避 (`retry_after_5s`) | `app/embedding/base.py::_run_with_retry` | ✅ | `test_embedding_service_retry_on_transient_error` (修复后) |
| 维度不匹配检测 | `_parse_embeddings` | ✅ | `test_pgvector_dimension_mismatch_raises_specific_error` |
| 多模态 (text+image) | `MultimodalEmbeddingRequest` | ✅ | `test_embedding` |
| 稀疏嵌入 | `SparseEmbeddingResponse` | ✅ | `test_embedding` |
| 健康检查 + TTL 模型信息缓存 | `BaseHttpEmbeddingService.get_model_info` | ✅ | `test_embedding` |
| BUSI 数据集适配 | `app/embedding/busi_adapter.py` | ✅ | `test_vector_index::TestBUSIAdapter` |
| 模型版本检测 `EMBEDDING_CONTRADICTION_THRESHOLD_{model}` | 设计文档 §1.1 | ⚠️ P2 | 阈值校准待 Gate 0 完成 |

### 4.2 偏离项

| # | 偏离 | 严重度 | 处置 |
|---|---|---|---|
| E-D1 | `EMBEDDING_CONTRADICTION_THRESHOLD_{model}` 配置项未实现 | P2 | 待 Gate 0 校准 |
| E-D2 | M³Bind 多模态方案 | P2 | 二期增强项 |
| E-D3 | 异质图 GNN 编码器（Ultrasound-CLIP 内置） | P2 | 依赖 Ultrasound-CLIP 完整版部署 |

### 4.3 真实临床场景测试 (EMB 部分)

`TestEmbeddingPipelineScenarios` 覆盖:

| 场景 | 验证点 | 状态 |
|---|---|---|
| Qdrant 不可用 + auto_fallback=True | 抛出 `VectorIndexError`（db_url="" 强制失败） | ✅ |
| PGVector 维度不匹配 | `DIMENSION_MISMATCH` 错误码 + `retryable=False` | ✅ |
| 瞬时错误重试 (CONNECTION_FAILED retryable=True) | 调用 2 次后成功 | ✅ (修复后) |
| 永久错误不重试 (DIMENSION_MISMATCH retryable=False) | 调用 1 次后抛错 | ✅ |
| Ultrasound-CLIP 不可用降级到 bge-m3-medical | 图像→纯文本 | ✅ |

---

## 5. 测试用例设计原则

`debug-pro-1.0.0` 测试用例遵循：

| 原则 | 实现方式 |
|---|---|
| **场景真实性** | 引用真实临床工作流（TI-RADS/LI-RADS 分级、TI-RADS 4b 特征、桥本甲状腺炎、HCC 三联征、胺碘酮致甲功异常） |
| **端到端覆盖** | emb→rag→knowledge 三管线均启动 mock，调用链与生产一致 |
| **器官特异标记** | MockEmbeddingService 改用 `_THYROID_MARKERS / _LIVER_MARKERS / _HEART_MARKERS` 集合打分，避免"首匹配子串"被跨器官查询误判 |
| **嵌入降级** | 主方案 (Ultrasound-CLIP) → 强兜底 (bge-m3-medical) → 终极兜底 (BM25) 链路完整 |
| **重排序降级** | bge-reranker 5xx → HeuristicReranker (cosine 0.5 + jaccard 0.5 + query_embed_fn) |
| **KB 版本控制** | safety_critical 即时失效 / 会话级版本锁定 / 4 步缓存读取判定链 |
| **跨器官关联规则** | cross-001 (甲亢肝损害) / cross-002 (胺碘酮→甲功异常) 通过 disease_code 触发 |

### 5.1 测试矩阵

| 类别 | 测试类 | 用例数 | 覆盖维度 |
|---|---|---|---|
| A 临床检索 | `TestClinicalRetrievalScenarios` | 7 | 甲状腺/肝脏/心脏 + 无器官过滤 |
| B Rerank 兜底 | `TestRerankFallbackScenarios` | 6 | bge 5xx 降级 / 权重校验 / cosine 边界 |
| C BM25 兜底 | `TestBM25FallbackScenarios` | 4 | 触发条件 3 路径 + 正常路径 |
| D KB 版本 | `TestKBVersionScenarios` | 9 | 解析/兼容性/4 步链/safety_critical |
| E EMB 降级 | `TestEmbeddingPipelineScenarios` | 5 | Qdrant/PGVector/重试/CLIP 降级 |
| F 跨器官规则 | `TestCrossOrganRuleScenarios` | 2 | 甲亢肝损害 / 胺碘酮甲功 |
| G 父子块 | `TestParentChildChunkScenarios` | 1 | child→parent 关联 |
| H 降级与异常 | `TestDegradationScenarios` | 3 | 证据不足 / dense 失败 / close |
| **合计** | 8 类 | **37** | **100% 通过** |

---

## 6. 关键风险与建议

### 6.1 已解决风险

| 风险 | 修复方式 | 验证 |
|---|---|---|
| `HeuristicReranker._cosine` self-norm bug | 改为 `_cosine_pair` (pairwise) | ✅ 测试覆盖 |
| 增强 query 误用于 rerank | 传 `request.query` (原始) | ✅ 测试覆盖 |
| BM25 索引装配断链 | 注入 `AsyncSessionLocal` + `UDAFVocabulary()` | ✅ 集成测试通过 |
| Mock 跨器官查询被错判 | 器官特异标记打分 | ✅ F3 测试通过 |

### 6.2 待跟进项 (非本期范围)

| 风险 | 一期方案 | 二期方案 |
|---|---|---|
| `graph_query` STUB | 仅 Protocol + warning 日志 | Neo4j Cypher / PGVector 递归 CTE |
| L2/L3 反馈驱动衰减 | 人工复审流程 | 自动衰减 + 复审队列 |
| `EMBEDDING_CONTRADICTION_THRESHOLD_{model}` | 统一 0.85 | 按模型维度校准 |
| CRAG 补充检索路由 | 仅预留接口 | Self-RAG 触发 + 知识查询智能体执行 |

---

## 7. 验收结论

### 7.1 通过标准核对

| 标准 | 状态 |
|---|---|
| 所有 debug-pro-1.0.0 测试用例 (37 个) 100% 通过 | ✅ |
| RAG 全量回归 (379 个) 0 失败 | ✅ |
| EMB + KB 全量回归 (314 个) 0 失败 | ✅ |
| 测试场景完全符合真实临床工作流 | ✅ |
| 测试 mock 与生产配置一致 (`query_embed_fn` 注入) | ✅ |
| Mock 器官判定采用特异标记词集合（非首匹配子串） | ✅ |
| KB 版本管理 2 层决策 + 4 步链覆盖完整 | ✅ |
| EMB 3 级降级链路 (CLIP → bge-m3 → BM25) 完整 | ✅ |
| Rerank 2 级降级 (bge-reranker → HeuristicReranker) 完整 | ✅ |

### 7.2 标签

- **Tag:** `debug-pro-1.0.0`
- **审计版本:** 完整 RAG / 知识库 / EMB 三管线
- **测试基线:** 37/37 通过
- **代码变更范围:** 仅 `tests/integration/rag/test_debug_pro_real_scenarios.py`（生产代码未改动）
- **生产代码偏离:** 已识别 4 项 RAG 偏离 + 5 项 KB 偏离 + 3 项 EMB 偏离，均已在历史修复或二期规划中处理

---

**报告生成时间:** 2026-06-30
**审查执行人:** debug-pro-1.0.0 自动化审查流程
**下次审查触发条件:** 任何 P0 偏离修复 / KB 二期 / EMB 嵌入模型升级
