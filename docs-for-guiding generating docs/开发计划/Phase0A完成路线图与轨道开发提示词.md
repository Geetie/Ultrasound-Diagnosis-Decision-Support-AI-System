# Phase 0A 完成路线图与轨道开发提示词

> **版本**：v1.0 | **日期**：2026-06-29
> **依据**：`开发计划.md` § 2.3 Phase 0A + `开发进度审查报告-20260629.md` 识别缺口
> **目标读者**：每个新开聊天窗口的开发协作者
> **设计原则**：接口先行、契约优先、AGENTS.md 四原则（Think / Simplicity / Surgical / Goal-Driven）

***

## 0. 使用说明

本文档包含两部分：

1. **路线图（Section 1-3）**：7 个执行波次，明确每个轨道任务的启动顺序与并行机会
2. **轨道开发提示词（Section 4-11）**：8 份可直接复制到新聊天窗口的提示词

**使用方法**：

- 启动 Wave 1 任务时，**每个提示词开一个独立聊天窗口**
- 在新聊天窗口中，将对应提示词**整段粘贴**作为首条用户消息
- 提示词已经包含所有必要的项目背景、依赖关系、验收标准，无需补充说明
- 完成后回到本项目窗口，汇报该轨道交付状态

***

## 1. Phase 0A 当前状态回顾

根据 2026-06-29 静态代码扫描：

| 轨道          | 状态     | 阻塞项                                                        |
| ----------- | ------ | ---------------------------------------------------------- |
| M-INF 基础设施  | ✅ 100% | —                                                          |
| M-LLM 推理    | 🟡 78% | 缺 T-GA-LLM-04 选型报告                                         |
| M-EMB 嵌入    | 🔴 33% | 缺 T-GA-EMB-01/02                                           |
| M-DATA 数据   | 🟡 56% | 缺 T-GA-DATA-01 构建管线                                        |
| M-RAG 检索    | 🔴 0%  | 整个 `app/rag/` 为空                                           |
| M-AGENT 智能体 | 🔴 0%  | 整个 `app/agents/` 为空                                        |
| M-FUS 融合层   | 🔴 33% | 仅 desensitization.py，缺 conflict/consistency/self\_rag/crag |
| M-SG 安全网关   | ✅ 100% | —                                                          |
| M-API 接口    | 🟡 86% | 缺报告生成模板（T-GB-API-02）                                       |
| M-OBS 可观测性  | ✅ 100% | —                                                          |
| M-SEC 安全合规  | 🟡 86% | —                                                          |

**结论**：Phase 0A 需启动 7 个并行轨道（DATA / EMB / RAG / AGT / FUS / LLM / INT）才能完成核心推理链路端到端集成（T-GA-INT-01）。

***

## 2. 轨道依赖图

```
                     ┌────────────────┐
                     │  T-G0-LLM-01   │ ✅ 已完成（app/inference/protocols.py）
                     └────────────────┘
                              │
                              ▼
Wave 1   ┌──→ DATA-01 ─┐      AGT-01 ──┐
         │              │               │
         │              └→ RAG-01 ←────┤
         │                              │
         ├──→ EMB-01 ──→ EMB-02 ──→ RAG-02 ──→ RAG-03
         │                              │              │
         ├──→ LLM-04 (独立)             │              │
         │                              │              │
         └──────────────────────────────┼──────────────┤
                                        │              │
Wave 2                                   ├──→ AGT-02 ←┤
                                        ├──→ AGT-03 ←┤
                                        └──→ AGT-07 ←┘
                                                    │
                                                    ▼
Wave 3                                          AGT-04 / AGT-05 / AGT-06（三专科并行）
                                                    │
                                                    ▼
Wave 4                                          FUS-01
                                                    │
                                                    ▼
Wave 5                                          FUS-02
                                                    │
                                                    ▼
Wave 6                                          INT-01（端到端集成）
```

***

## 3. 执行波次（Wave Plan）

| Wave       | 并行轨道                         | 前置依赖            | 启动条件                           | 验收产物                                |
| ---------- | ---------------------------- | --------------- | ------------------------------ | ----------------------------------- |
| **Wave 1** | DATA-01、EMB-01、LLM-04、AGT-01 | 全部为零依赖          | T-G0-\* 已完成                    | 知识库构建脚本、CLIP 微调产物、选型报告、Protocol ABC |
| **Wave 2** | RAG-01、EMB-02、AGT-02、AGT-07  | Wave 1          | DATA-01 + EMB-01 + AGT-01 完成   | 查询增强模块、Qdrant 客户端、调度器、监控器           |
| **Wave 3** | RAG-02、AGT-03                | Wave 2          | EMB-02 + RAG-01 + AGT-01 完成    | 多模态检索服务、知识查询智能体                     |
| **Wave 4** | RAG-03                       | Wave 3          | RAG-02 完成                      | Cross-Encoder 精排、BM25 兜底            |
| **Wave 5** | AGT-04、AGT-05、AGT-06、FUS-01  | Wave 4 + Wave 2 | RAG-03 + AGT-02 完成             | 三专科智能体、融合层核心逻辑                      |
| **Wave 6** | FUS-02                       | Wave 5          | FUS-01 + RAG-03 完成             | Self-RAG + CRAG 模块                  |
| **Wave 7** | INT-01                       | Wave 6          | FUS-02 + SG-02 ✅ + API-02 ✅ 完成 | 端到端诊断流程（单器官 P99<5s）                 |

**关键观察**：

- AGT-01 是唯一在 Wave 1 即可启动的智能体任务（仅依赖 T-G0-LLM-01）
- FUS-01 必须等 AGT-04 至少一个专科智能体存在才能启动
- INT-01 必须在 FUS-02 + SG-02 + API-02 都完成后才能启动
- LLM-04（选型报告）独立，可任何时候完成

**Wave 1 可同时启动 4 个独立聊天窗口**（最大化 TRAE 并行能力）。

***

# Section II: 轨道开发提示词

> 以下 8 份提示词按 Wave 顺序排列。每份提示词为独立单元，可直接复制使用。

***

## 4. 公共规范（所有提示词通用）

所有轨道开发提示词须嵌入以下公共规范：

### 4.1 代码规范（来自 AGENTS.md）

1. **Think Before Coding**：在写代码前，先输出"假设与权衡"段落
2. **Simplicity First**：最小代码解决问题，200 行可 50 行则重写
3. **Surgical Changes**：仅修改必要代码，匹配现有风格，不"改进"无关代码
4. **Goal-Driven Execution**：每个任务定义可验证的验收标准，循环至验证通过

### 4.2 接口契约规范

- 所有 Protocol/ABC 必须先于实现编写
- 输入/输出使用 Pydantic v2 BaseModel
- 自定义异常类必须携带 `error_code: str`（遵循 E1xxx-E4xxx 错误码体系）
- 性能契约必须显式标注 P99 延迟预算

### 4.3 错误码体系

| 前缀    | 含义       | 示例                       |
| ----- | -------- | ------------------------ |
| E1xxx | 客户端/请求错误 | E1001 参数错误，E1003 鉴权失败    |
| E2xxx | 服务端/资源错误 | E2001 DB 连接，E2002 EMB 超时 |
| E3xxx | 业务/规则错误  | E3001 同意撤回，E3002 禁忌触发    |
| E4xxx | 上游/依赖错误  | E4005 SafetyGateway 不可用  |

### 4.4 提交规范

```bash
git checkout -b feature/T-GA-{轨道}-{任务编号}-{描述}
git add .
git commit -m "feat({轨道}): {任务ID} - {简述}"
git push origin feature/...
```

### 4.5 测试规范

- 单元测试覆盖率：核心模块 ≥ 90%，全部 ≥ 80%
- 每个新增文件必须配套单元测试
- 测试文件命名：`test_{module_name}.py`
- Mock 所有外部依赖（DB / Redis / LLM / Embedding）
- 性能契约测试用 `pytest-benchmark` 或显式 `time.perf_counter()`

### 4.6 文档同步

- 完成时同步更新 `开发计划.md` 中对应任务状态
- 在 `archive_of_achivement/` 下创建 `archive_phase0A_{轨道}_{日期}/PROJECT_ARCHIVE.md`

***

## 5. 提示词 #1：DATA 轨道（T-GA-DATA-01 知识库构建管线）

> **Wave 1 启动** | 预计工作量：2-3 天 | 依赖：✅ T-G0-DATA-01 已完成

***

### 5.1 提示词正文（直接复制使用）

````markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深 Python 后端工程师，专精医疗 NLP 与知识库工程。

# 项目背景（必读）
你正在开发一个**面向临床的超声诊断决策支持系统**。项目技术栈：
- 后端：Python 3.11+ / FastAPI / SQLAlchemy 2.0 (async) / Pydantic v2
- 数据库：PostgreSQL 16 + PGVector + Alembic
- 缓存：Redis 7
- LLM：llama.cpp + vLLM 双引擎
- 嵌入：Ultrasound-CLIP（图像）+ bge-m3-medical（文本）
- 严格遵守 `AGENTS.md` 四原则（Think/Simplicity/Surgical/Goal-Driven）

# 当前任务
完成 **T-GA-DATA-01：知识库构建管线（分块+特征提取+混合索引）**。
验收标准：**索引构建完成，检索可用**（P99<100ms）。

# 项目结构（关键路径）
- `app/knowledge/udt_parser.py` ← ✅ 已实现，UDT 解析器
- `app/knowledge/vocabulary.py` ← ✅ 已实现，UDAF 词汇表
- `app/knowledge/version.py` ← ✅ 已实现，KBVersion + parse_kb_version
- `app/knowledge/baseline_validation.py` ← ✅ 已实现
- `app/knowledge/segmentation_validation.py` ← ✅ 已实现
- `app/knowledge/baseline_annotation.py` ← ✅ 已实现
- `app/models/database.py` ← ✅ 已实现，DiagnosisSession / AuditLog 等 ORM
- `app/config/database.py` ← ✅ 已实现，async_engine + get_db_session
- `app/config/settings.py` ← ✅ 已实现
- `app/inference/protocols.py` ← ✅ 已实现 InferenceEngine Protocol（参考风格）
- `app/embedding/protocols.py` ← ✅ 已实现 EmbeddingModelType / EmbeddingModelInfo
- `alembic/versions/` ← ✅ 5 个迁移文件已完成
- `scripts/` ← ⚠️ 缺 init_knowledge_base.py（本次任务产物之一）

# 详细步骤

## Step 1：先思考（Think Before Coding）
在写代码前，先输出一份简短的"假设与权衡"清单（5-10 条），覆盖：
- 文本分块策略：固定窗口 vs 句子边界 vs 语义边界
- 特征提取：是否使用 LLM 生成摘要、是否抽取元数据
- 索引结构：单表 vs 分表（按器官）、是否预计算向量
- 增量更新：全量重建 vs 差分更新
- 数据源：JSON 文件、Markdown 文档、还是数据库
- 性能预算：构建时长、磁盘占用、检索 P99 延迟

## Step 2：阅读现有代码
至少通读以下文件，理解现有风格：
- `app/knowledge/udt_parser.py`（UDF 解析器接口）
- `app/knowledge/vocabulary.py`（UDAF 词汇表结构）
- `app/knowledge/version.py`（KBVersion 使用模式）
- `app/embedding/protocols.py`（Protocol 定义风格）
- `app/inference/protocols.py`（Protocol 定义风格）

## Step 3：设计 Protocol 接口
在 `app/knowledge/protocols.py` 创建（**先于实现**）：

```python
from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel

class KnowledgeChunk(BaseModel):
    chunk_id: str
    kb_version: str  # kb_v{major}.{minor}.{patch}
    organ: str  # thyroid / liver / cardiac
    content: str
    metadata: dict
    embedding: Optional[List[float]] = None  # bge-m3-medical 768d
    source_doc_id: str
    section_path: str  # e.g. "TI-RADS/Category 4/Subcategory 4A"
    token_count: int

class IndexBuildRequest(BaseModel):
    kb_version: str
    source_dir: str  # e.g. "data/kb_sources/v1.0.0/"
    organs: List[str]  # 限定器官，空表示全部
    force_rebuild: bool = False
    embedding_model: str = "bge-m3-medical"

class IndexBuildResult(BaseModel):
    kb_version: str
    total_chunks: int
    organ_breakdown: dict
    build_duration_seconds: float
    index_path: str

class KnowledgeIndexBuilder(ABC):
    @abstractmethod
    async def build(self, request: IndexBuildRequest) -> IndexBuildResult: ...
    
    @abstractmethod
    async def validate(self, kb_version: str) -> bool: ...
````

## Step 4：实现分块器 `app/knowledge/chunker.py`

- 支持 3 种策略：fixed\_window / sentence\_boundary / semantic（用 UDT 边界）
- 默认使用 sentence\_boundary，分块长度 256-512 tokens
- 输出 KnowledgeChunk 列表，保留 source\_doc\_id + section\_path

## Step 5：实现特征提取器 `app/knowledge/feature_extractor.py`

- 抽取：器官、TI-RADS/LI-RADS 类别、关键测量值、来源文档 ID
- 复用 `app/knowledge/udt_parser.py` 已实现的 UDT 解析
- 不得重复实现 UDT 解析逻辑

## Step 6：实现混合索引器 `app/knowledge/hybrid_indexer.py`

- 文本部分：使用 bge-m3-medical 生成 embedding
- 元数据部分：存储到 PG（不向量化）
- 向量部分：暂存到 PGVector（Qdrant 在 EMB 轨道实现后再切换）
- 表结构迁移：在 `alembic/versions/0006_knowledge_chunks.py` 中创建

## Step 7：实现构建管线编排器 `app/knowledge/builder.py`

- 串联 chunker → feature\_extractor → hybrid\_indexer
- 支持断点续传：每个 chunk 写入后立即 commit
- 输出 IndexBuildResult + 索引统计

## Step 8：实现 CLI 脚本 `scripts/init_knowledge_base.py`

- 接受参数：`--kb-version`, `--source-dir`, `--organs`, `--force-rebuild`
- 调用 builder
- 输出构建进度日志
- 完成后打印 IndexBuildResult

## Step 9：编写测试 `tests/unit/data/test_knowledge_builder.py`

- 测试 3 种分块策略
- 测试特征提取（UDF 词汇命中、器官识别）
- 测试混合索引（文本/向量/元数据一致性）
- 测试断点续传
- 测试版本不匹配时的拒绝行为
- Mock 所有外部依赖（PG / Redis / Embedding 模型）

## Step 10：编写迁移 `alembic/versions/0006_knowledge_chunks.py`

- 创建 `knowledge_chunks` 表（含 kb\_version / organ / content / metadata / embedding）
- 创建 kb\_version 索引
- 创建 organ 索引

## Step 11：验证（Goal-Driven Execution）

- 运行 `pytest tests/unit/data/test_knowledge_builder.py -v` 全过
- 运行 `python scripts/init_knowledge_base.py --kb-version kb_v1.0.0 --source-dir data/kb_sources/v1.0.0/` 成功
- 通过 PG 查询确认 chunks 已写入
- 运行 `python -c "from app.knowledge.protocols import KnowledgeIndexBuilder; print('OK')"` 验证导入

# 验收标准（必须全部满足）

- [ ] `app/knowledge/protocols.py` 定义完整 Protocol
- [ ] `app/knowledge/chunker.py` 实现 3 种分块策略
- [ ] `app/knowledge/feature_extractor.py` 复用 UDT 解析器
- [ ] `app/knowledge/hybrid_indexer.py` 实现混合索引
- [ ] `app/knowledge/builder.py` 串联完整管线
- [ ] `scripts/init_knowledge_base.py` CLI 可执行
- [ ] `alembic/versions/0006_knowledge_chunks.py` 迁移可成功运行
- [ ] `tests/unit/data/test_knowledge_builder.py` 全过，覆盖率 ≥ 90%
- [ ] 索引构建时间（1000 chunks 测试集）< 60s
- [ ] 检索单 chunk P99 < 100ms（mock embedding）

# 提交规范

完成后：

1. `git add . && git commit -m "feat(DATA): T-GA-DATA-01 - 知识库构建管线"`
2. 推送到 `feature/T-GA-DATA-01-kb-builder` 分支
3. 在 `archive_of_achivement/archive_phase0A_data_{日期}/PROJECT_ARCHIVE.md` 写归档报告
4. 在 `开发计划.md` 中标记 T-GA-DATA-01 为 ✅

# 风险与依赖

- 风险：分块策略选错会导致检索召回率低。建议先实现 sentence\_boundary，再加其他作为备选
- 风险：特征提取调用 LLM 会很慢，初版不要用 LLM 抽取元数据，用 UDT 规则即可
- 后续依赖：完成后立即通知下游 RAG 轨道（T-GA-RAG-01 依赖本任务）

***

## 6. 提示词 #2：EMB 轨道（T-GA-EMB-01/02 微调+索引）

> **Wave 1 启动** | 预计工作量：5-7 天（含 8 小时连续运行验证） | 依赖：T-G0-EMB-01 授权（30 天硬期限）

***

### 6.1 提示词正文

````markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深 ML 工程师，专精医学影像多模态模型与向量检索。

# 项目背景（必读）
- 任务：完成 T-GA-EMB-01（Ultrasound-CLIP 院内微调+I2T@10 验证 8 小时连续运行）+ T-GA-EMB-02（Qdrant 向量索引构建）
- 技术栈：Ultrasound-CLIP（图像→文本）、bge-m3-medical（纯文本）、Qdrant（向量库）
- 已有：`app/embedding/protocols.py`（EmbeddingModelType / EmbeddingModelInfo 定义）
- 已有：`app/embedding/bge_m3.py`、`app/embedding/ultrasound_clip.py`（基础接口）
- 已有：`app/embedding/base.py`（基础抽象）
- 验收：I2T@10≥37%（开箱）/≥45%（微调） + 检索 P99<100ms

# 详细步骤

## Step 1：先思考（输出假设清单）
- 微调数据来源：院内 8 小时数据 vs 公开数据集（BUSI、Thyroid Ultrasound）
- 微调策略：全量微调 vs LoRA（推荐 LoRA，节省显存）
- 训练时长：8 小时连续运行的可行性
- 验证指标：I2T@10 怎么定义、测试集怎么划分
- Qdrant 部署：本地 Docker vs 远程集群
- Qdrant 与 PGVector 的关系：主备？镜像？

## Step 2：阅读现有代码
- `app/embedding/protocols.py`（必须严格遵守 Protocol 风格）
- `app/embedding/ultrasound_clip.py`（基础接口）
- `app/embedding/bge_m3.py`（基础接口）
- `app/inference/protocols.py`（参考 Protocol 风格）

## Step 3：完成 T-GA-EMB-01：Ultrasound-CLIP 院内微调

### Step 3.1：扩展 `app/embedding/ultrasound_clip.py`
- 实现 `UltrasoundCLIPModel` 类，封装 CLIP 模型加载、推理、微调接口
- 实现 `finetune(dataset_path, output_dir, epochs, batch_size, learning_rate)` 方法
- 实现 `evaluate(test_set_path, k=10)` 方法返回 I2T@10

### Step 3.2：创建微调脚本 `scripts/finetune_ultrasound_clip.py`
- 接受参数：`--dataset-path`, `--output-dir`, `--epochs`, `--batch-size`, `--learning-rate`
- 输出：微调后模型 + 训练日志 + I2T@10 评估报告

### Step 3.3：创建 8 小时连续运行验证脚本 `tests/integration/emb/test_ultrasound_clip_sustained.py`
- 每小时记录一次 I2T@10 + 显存占用 + 推理延迟
- 8 小时后输出稳定性报告
- 标记为 `sustained_run`（需手动启用：`SUSTAINED_RUN_ENABLED=1 pytest -m sustained_run`）

## Step 4：完成 T-GA-EMB-02：Qdrant 向量索引

### Step 4.1：扩展 `app/embedding/protocols.py`
新增（**不破坏现有 API**）：

```python
class VectorIndexBackend(str, Enum):
    PGVECTOR = "pgvector"
    QDRANT = "qdrant"

class VectorIndexClient(ABC):
    @abstractmethod
    async def upsert(self, collection: str, vectors: List[List[float]], metadata: List[dict], ids: List[str]) -> None: ...
    
    @abstractmethod
    async def search(self, collection: str, query_vector: List[float], top_k: int = 10, filter: Optional[dict] = None) -> List[dict]: ...
    
    @abstractmethod
    async def delete_collection(self, collection: str) -> None: ...
````

### Step 4.2：实现 `app/embedding/qdrant_client.py`

- 使用 `qdrant-client` 库（添加到 `requirements.txt`）
- 异步包装 `QdrantClient`
- 实现 Protocol 三方法
- 配置：连接 Qdrant 服务（默认 localhost:6333）

### Step 4.3：实现 `app/embedding/pgvector_client.py`

- 使用 `pgvector` 库
- 异步实现 Protocol 三方法
- 作为 Qdrant 的紧凑模式降级方案

### Step 4.4：实现 `app/embedding/index_factory.py`

- `create_index_client(backend: VectorIndexBackend) -> VectorIndexClient`
- 根据 settings 决定使用 Qdrant 或 PGVector

### Step 4.5：编写测试 `tests/unit/emb/test_vector_index.py`

- 测试 Qdrant 与 PGVector 一致性（同输入应得相似 TopK）
- 测试 upsert/search/delete 流程
- 测试 filter 查询
- Mock 真实 Qdrant 服务（使用 `mongomock` 或 `qdrant-client-mock`）

## Step 5：编写 Docker Compose 片段

在 `docker-compose.dev.yml` 中添加 Qdrant 服务：

```yaml
qdrant:
  image: qdrant/qdrant:v1.7.0
  ports:
    - "6333:6333"
  volumes:
    - qdrant_data:/qdrant/storage
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:6333/health"]
    interval: 10s
```

## Step 6：验证

- 微调脚本能成功跑通（用小数据集验证流程）
- I2T\@10 评估脚本可输出报告
- Qdrant 与 PGVector 单元测试全过
- 检索 P99 < 100ms（mock 1000 条向量）

# 验收标准

- [ ] T-GA-EMB-01：Ultrasound-CLIP 院内微调脚本可执行
- [ ] T-GA-EMB-01：I2T\@10 ≥ 45%（微调后）
- [ ] T-GA-EMB-01：8 小时连续运行无崩溃（I2T\@10 波动 < 5%）
- [ ] T-GA-EMB-02：Qdrant 客户端实现 Protocol
- [ ] T-GA-EMB-02：PGVector 客户端实现 Protocol（紧凑模式降级）
- [ ] T-GA-EMB-02：docker-compose.dev.yml 包含 Qdrant 服务
- [ ] 检索 P99 < 100ms（1000 条测试向量）
- [ ] 单元测试覆盖率 ≥ 85%

# 提交规范

完成后：

1. `git commit -m "feat(EMB): T-GA-EMB-01/02 - CLIP微调+Qdrant索引"`
2. 在 `archive_of_achivement/archive_phase0A_emb_{日期}/PROJECT_ARCHIVE.md` 写归档报告
3. 包含：微调前后 I2T\@10 对比、显存占用曲线、检索延迟 P99 报告

# 风险与依赖

- 风险 R-01：Ultrasound-CLIP 商业授权失败 → 自动切换 bge-m3-medical 主方案
- 风险：8 小时连续运行可能出现显存碎片化 → 参考 `app/inference/vram_fragmentation.py` 实现监控
- 后续依赖：完成后立即通知 RAG 轨道（T-GA-RAG-02 多模态检索依赖 EMB-02）

***

## 7. 提示词 #3：RAG 轨道（T-GA-RAG-01/02/03 完整管线）

> **Wave 2 启动** | 预计工作量：5-7 天 | 依赖：DATA-01 + EMB-02 + AGT-01

***

### 7.1 提示词正文

```markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深算法工程师，专精 RAG（Retrieval-Augmented Generation）系统。

# 项目背景（必读）
- 任务：完成 T-GA-RAG-01（查询增强）+ T-GA-RAG-02（多模态并行检索）+ T-GA-RAG-03（Cross-Encoder 精排+BM25 兜底）
- 验收：混合检索召回率 ≥ 90%、精排后 Top5 准确率 ≥ 85%、扩展后检索召回率提升 ≥ 10%
- 已有：`app/embedding/`（含 bge_m3 / ultrasound_clip / qdrant_client / pgvector_client）
- 已有：`app/knowledge/`（含 UDT 解析、UDAF 词汇、版本管理、知识库构建器）
- 已有：`app/inference/protocols.py`（参考 Protocol 风格）

# 详细步骤

## Step 1：先思考（输出假设清单）
- 查询增强：同义词库来源、扩展层数（1 跳 vs 2 跳）、是否使用 LLM 扩展
- 多模态融合：向量加权（Dense vs Sparse 比例）、是否需要 RRF（Reciprocal Rank Fusion）
- 精排模型：Cross-Encoder 选型（bge-reranker-base vs bge-reranker-large）
- BM25 兜底：何时触发（Top5 全 < 阈值？召回率 < 阈值？）
- 缓存策略：哪些层需要缓存、TTL 多长

## Step 2：阅读现有代码
- `app/embedding/protocols.py`（必须使用 EmbeddingModelType / EmbeddingModelInfo）
- `app/embedding/index_factory.py`（如已存在；如不存在需先实现）
- `app/knowledge/udt_parser.py`（查询增强可复用）
- `app/knowledge/vocabulary.py`（同义词库来源）

## Step 3：创建 `app/rag/` 包结构

```

app/rag/
├── __init__.py
├── protocols.py        # RAG Service Protocol
├── query\_enhancer.py   # T-GA-RAG-01
├── retriever.py        # T-GA-RAG-02
├── reranker.py         # T-GA-RAG-03
├── bm25\_fallback.py    # T-GA-RAG-03
└── service.py          # 编排器

````

## Step 4：实现 `app/rag/protocols.py`

```python
from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel
from app.knowledge.protocols import KnowledgeChunk

class RetrievalRequest(BaseModel):
    query: str
    organ: Optional[str] = None
    top_k: int = 10
    use_query_enhancement: bool = True
    use_reranking: bool = True
    use_bm25_fallback: bool = True
    min_score_threshold: float = 0.5

class RetrievalResult(BaseModel):
    chunks: List[KnowledgeChunk]
    scores: List[float]
    retrieval_method: str  # "dense" / "sparse" / "hybrid" / "bm25_fallback"
    enhanced_query: Optional[str] = None
    latency_ms: float

class RAGService(ABC):
    @abstractmethod
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...
    
    @abstractmethod
    async def health_check(self) -> bool: ...
````

## Step 5：实现 T-GA-RAG-01 `app/rag/query_enhancer.py`

### 步骤：

1. 加载 UDAF 词汇表（同义词、上位词、相关特征）
2. 解析查询：识别器官名、关键特征词
3. 扩展查询：同义词 + 上位词 + 相关特征（3 层）
4. 复用 `app/knowledge/udt_parser.py` 的 UDT 解析（**不得重复实现**）
5. 返回增强后的 query 字符串

### 验收：扩展后检索召回率提升 ≥ 10%（与原始 query 对比）

## Step 6：实现 T-GA-RAG-02 `app/rag/retriever.py`

### 步骤：

1. 实现 Dense 检索：调用 bge-m3-medical embedding → Qdrant search
2. 实现 Sparse 检索：BM25 over content 文本
3. 实现 Hybrid 融合：使用 RRF（Reciprocal Rank Fusion）算法
4. 支持多模态：当 query 包含图像时，调用 Ultrasound-CLIP
5. 支持器官过滤：通过 Qdrant filter

### 验收：混合检索召回率 ≥ 90%（测试集 1000 条）

## Step 7：实现 T-GA-RAG-03 `app/rag/reranker.py`

### 步骤：

1. 使用 bge-reranker-base（或同等级 Cross-Encoder）
2. 输入：retriever 输出的 TopK=20
3. 输出：精排后的 TopK=5
4. 必须返回每个 chunk 的精排分数

### 验收：精排后 Top5 准确率 ≥ 85%

## Step 8：实现 T-GA-RAG-03 `app/rag/bm25_fallback.py`

### 步骤：

1. 当 retriever 召回率 < 阈值 或 Top1 score < 0.3 时触发
2. 使用 `rank_bm25` 库
3. 索引来源：knowledge\_chunks 表的 content 字段
4. 触发条件写入 `RAGService` 配置项

## Step 9：实现 `app/rag/service.py` 编排器

- 串联 query\_enhancer → retriever → reranker → bm25\_fallback
- 实现 P99 延迟监控埋点
- 实现 RAGService Protocol 完整接口

## Step 10：编写测试

### `tests/unit/rag/test_query_enhancer.py`

- 测试同义词扩展
- 测试器官识别
- 测试 UDAF 词汇命中率

### `tests/unit/rag/test_retriever.py`

- 测试 Dense / Sparse / Hybrid 三种模式
- 测试多模态查询（含图像）
- 测试器官过滤
- Mock Qdrant / BM25

### `tests/unit/rag/test_reranker.py`

- 测试精排分数
- 测试 TopK 重排
- Mock Cross-Encoder 模型

### `tests/integration/rag/test_rag_e2e.py`

- 端到端：1000 条测试查询
- 验证召回率 ≥ 90%
- 验证 P99 延迟 < 100ms

## Step 11：验证

- `pytest tests/unit/rag tests/integration/rag -v` 全过
- 启动 `app.state.rag_service = RAGServiceImpl()` 在 `app/main.py` 的 lifespan 中
- 调用 `await rag_service.health_check()` 返回 True

# 验收标准

- [ ] T-GA-RAG-01：查询增强召回率提升 ≥ 10%
- [ ] T-GA-RAG-02：混合检索召回率 ≥ 90%
- [ ] T-GA-RAG-03：精排后 Top5 准确率 ≥ 85%
- [ ] BM25 兜底：触发条件正确
- [ ] 检索 P99 < 100ms
- [ ] 单元测试覆盖率 ≥ 90%
- [ ] 端到端集成测试通过

# 提交规范

完成后：

1. `git commit -m "feat(RAG): T-GA-RAG-01/02/03 - 完整RAG管线"`
2. 在 `archive_of_achivement/archive_phase0A_rag_{日期}/PROJECT_ARCHIVE.md` 写归档报告
3. 包含：召回率/准确率/延迟报告、混合检索与单一检索对比

# 风险与依赖

- 风险：Cross-Encoder 模型加载耗时 → 使用单例 + 懒加载
- 风险：BM25 在大语料上慢 → 限制 chunk 数（默认 10000）
- 后续依赖：完成后通知 AGT 轨道（T-GA-AGT-03 知识查询智能体依赖 RAG-02）

***

## 8. 提示词 #4：AGT 基础智能体（T-GA-AGT-01/02/03/07）

> **Wave 1+2 启动** | 预计工作量：7-10 天 | 依赖：T-G0-LLM-01 ✅

***

### 8.1 提示词正文

```markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深 AI 架构师，专精多智能体系统（Multi-Agent System）设计。

# 项目背景（必读）
- 任务：完成 T-GA-AGT-01（智能体 Protocol）+ T-GA-AGT-02（中央调度）+ T-GA-AGT-03（知识查询）+ T-GA-AGT-07（流程监控）
- 设计原则：接口先行、契约优先、单一职责
- 已有：`app/inference/protocols.py`（InferenceEngine Protocol）
- 已有：`app/inference/factory.py`（create_inference_engine_from_settings）
- 已有：`app/services/safety_gateway_client.py`（SafetyGatewayClient，可被智能体调用）
- 已有：`app/api/v1/diagnosis.py`（诊断接口的请求/响应结构）
- 已有：`app/models/schemas.py`（DiagnosisRequest / DiagnosisResult / Organ 枚举）

# 详细步骤

## Step 1：先思考（输出假设清单）
- 智能体通信：异步消息队列（Redis Pub/Sub） vs 直接调用
- 中央调度：基于器官 vs 基于功能（load_balance vs route）
- 知识查询智能体：是否复用 RAG Service（T-GA-RAG-02）
- 流程监控：埋点位置（AOP 装饰器 vs 显式调用）
- 错误传播：智能体失败如何降级

## Step 2：阅读现有代码
- `app/inference/protocols.py`（**必须严格匹配 Protocol 风格**）
- `app/inference/factory.py`（参考工厂模式）
- `app/services/safety_gateway_client.py`（参考异步客户端封装）
- `app/models/schemas.py`（DiagnosisRequest / DiagnosisResult）
- `app/api/v1/diagnosis.py`（理解调用流程）

## Step 3：创建 `app/agents/` 包结构

```

app/agents/
├── __init__.py
├── protocols.py        # T-GA-AGT-01
├── dispatcher.py       # T-GA-AGT-02
├── knowledge.py        # T-GA-AGT-03
├── monitor.py          # T-GA-AGT-07
├── specialist/         # T-GA-AGT-04/05/06（另开聊天窗口）
│   ├── __init__.py
│   ├── base.py         # SpecialistAgent 抽象基类
│   ├── thyroid.py
│   ├── liver.py
│   └── cardiac.py
└── prompts/            # 各智能体的 system prompt
├── dispatcher.md
├── knowledge.md
├── monitor.md
├── thyroid.md
├── liver.md
└── cardiac.md

````

## Step 4：实现 T-GA-AGT-01 `app/agents/protocols.py`

```python
from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel
from app.models.schemas import Organ

class AgentContext(BaseModel):
    request_id: str
    session_id: str
    organ: Organ
    user_query: str
    imaging_metadata: Optional[dict] = None
    prior_messages: List[dict] = []

class AgentResponse(BaseModel):
    agent_name: str
    organ: Optional[Organ]
    content: str
    confidence: float
    evidence_chunk_ids: List[str] = []
    reasoning_trace: List[str] = []
    latency_ms: float

class Agent(ABC):
    @abstractmethod
    async def invoke(self, context: AgentContext) -> AgentResponse: ...
    
    @abstractmethod
    def get_capability_tags(self) -> List[str]: ...

class DispatcherAgent(ABC):
    @abstractmethod
    async def route(self, request: DiagnosisRequest) -> Organ: ...
    
    @abstractmethod
    async def aggregate(self, specialist_responses: List[AgentResponse]) -> AgentResponse: ...

class KnowledgeAgent(ABC):
    @abstractmethod
    async def query(self, query: str, organ: Organ, top_k: int = 10) -> List[KnowledgeChunk]: ...

class MonitorAgent(ABC):
    @abstractmethod
    async def track_diagnosis(self, request: DiagnosisRequest, response: DiagnosisResult) -> None: ...
    
    @abstractmethod
    async def detect_anomaly(self, request: DiagnosisRequest) -> Optional[str]: ...
````

## Step 5：实现 T-GA-AGT-02 `app/agents/dispatcher.py`

### 步骤：

1. 路由逻辑：基于 DiagnosisRequest.organ 字段
2. 路由表：常量 `ORGAN_TO_SPECIALIST = {Organ.THYROID: "thyroid", Organ.LIVER: "liver", Organ.CARDIAC: "cardiac"}`
3. 聚合逻辑：调用多个 specialist 后，融合结果（先简单平均 confidence，后续由 FUS 轨道替换）
4. 验收：路由正确率 100%（单器官测试）

### 关键代码骨架：

```python
class DispatcherAgentImpl(DispatcherAgent):
    def __init__(self, specialists: Dict[Organ, Agent]):
        self._specialists = specialists
    
    async def route(self, request) -> Organ:
        return request.organ  # 当前简单路由
    
    async def aggregate(self, responses: List[AgentResponse]) -> AgentResponse:
        # 简化的聚合，后续由 FUS 轨道替换
        ...
```

## Step 6：实现 T-GA-AGT-03 `app/agents/knowledge.py`

### 步骤：

1. 封装 RAG Service（T-GA-RAG-02 完成后接入）
2. 实现两层缓存：Redis（TTL 300s）+ 进程内 LRU（容量 100）
3. 验收：缓存命中率 ≥ 70%（测试集 1000 条）

### 关键代码骨架：

```python
class KnowledgeAgentImpl(KnowledgeAgent):
    def __init__(self, rag_service: RAGService, redis: Redis):
        self._rag = rag_service
        self._redis = redis
        self._local_cache = LRUCache(maxsize=100)
    
    async def query(self, query: str, organ: Organ, top_k: int = 10) -> List[KnowledgeChunk]:
        cache_key = f"kb:cache:{organ}:{hash(query)}"
        # 1. 查 Redis
        cached = await self._redis.get(cache_key)
        if cached:
            return json.loads(cached)
        # 2. 查本地 LRU
        if cache_key in self._local_cache:
            return self._local_cache[cache_key]
        # 3. 查 RAG
        result = await self._rag.retrieve(RetrievalRequest(query=query, organ=organ, top_k=top_k))
        # 4. 写回缓存
        await self._redis.set(cache_key, json.dumps([c.dict() for c in result.chunks]), ex=300)
        self._local_cache[cache_key] = result.chunks
        return result.chunks
```

## Step 7：实现 T-GA-AGT-07 `app/agents/monitor.py`

### 步骤：

1. 实现诊断质量追踪：记录 latency、confidence、specialist、user\_feedback
2. 实现异常检测：
   - 异常 1：confidence < 0.3（标记 needs\_review）
   - 异常 2：latency > 5s（标记 performance\_degraded）
   - 异常 3：连续 10 次同一 organ 失败（标记 system\_anomaly）
3. 复用 `app/observability/metrics.py` 埋点
4. 验收：异常检测规则执行正确（单元测试覆盖 3 种异常）

## Step 8：创建 `app/agents/prompts/` 系统提示词

每个 .md 文件包含：

- 角色定位
- 输入/输出 schema
- 工具列表
- Few-shot 示例（3-5 个）
- 边界与降级策略

## Step 9：编写测试

### `tests/unit/agt/test_dispatcher.py`

- 路由正确性
- 聚合逻辑
- 错误处理（specialist 不可用时降级）

### `tests/unit/agt/test_knowledge.py`

- 缓存命中/未命中
- Redis 不可用时降级到本地缓存
- TTL 过期

### `tests/unit/agt/test_monitor.py`

- 3 种异常检测规则
- 埋点指标

## Step 10：验证

- `pytest tests/unit/agt -v` 全过
- 在 `app/main.py` 的 lifespan 中实例化 dispatcher、knowledge、monitor 智能体
- 调用 `await dispatcher.health_check()` 返回 True

# 验收标准

- [ ] T-GA-AGT-01：Protocol 完整定义
- [ ] T-GA-AGT-02：中央调度路由正确率 100%
- [ ] T-GA-AGT-03：知识查询缓存命中率 ≥ 70%
- [ ] T-GA-AGT-07：3 种异常检测规则正确
- [ ] 单元测试覆盖率 ≥ 90%
- [ ] 与 RAG Service 集成可用（即使 RAG 未完成，Knowledge Agent 也应有 mock 实现）

# 提交规范

完成后：

1. `git commit -m "feat(AGT): T-GA-AGT-01/02/03/07 - 智能体基础架构"`
2. 在 `archive_of_achivement/archive_phase0A_agt_foundation_{日期}/PROJECT_ARCHIVE.md` 写归档报告
3. 包含：Protocol 评审记录、路由表、缓存策略、异常检测规则

# 风险与依赖

- 风险：AGT-03 依赖 RAG-02 完整功能 → 初期用 mock 实现，RAG-02 完成后切换
- 后续依赖：完成后通知 AGT-Specialists 轨道（AGT-04/05/06 依赖 dispatcher）

***

## 9. 提示词 #5：AGT 专科智能体（T-GA-AGT-04/05/06 三器官）

> **Wave 5 启动** | 预计工作量：7-10 天（三器官可并行） | 依赖：AGT-02 + RAG-03

***

### 9.1 提示词正文

````markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深医学 AI 工程师，专精临床决策支持系统与多器官诊断模型。

# 项目背景（必读）
- 任务：完成 T-GA-AGT-04（甲状腺专科，TI-RADS 分级）+ T-GA-AGT-05（肝脏专科，LI-RADS 分级）+ T-GA-AGT-06（心脏专科，LVEF 评估）
- 验收：单器官诊断端到端 P99 < 5s
- 已有：`app/agents/dispatcher.py`、`app/agents/protocols.py`
- 已有：`app/agents/knowledge.py`（KnowledgeAgent）
- 已有：`app/rag/service.py`（RAGService）
- 已有：`app/inference/`（llama.cpp + vLLM）
- 已有：`app/knowledge/udt_parser.py`、`vocabulary.py`

# 详细步骤

## Step 1：先思考（输出假设清单）
- 每个专科的 system prompt：是否使用 RAG 检索作为上下文、是否包含 few-shot
- TI-RADS / LI-RADS 分级：是 LLM 推理 vs 规则引擎 vs 两者结合
- 置信度计算：基于 LLM 输出的 token 概率 vs 基于证据 chunk 数
- 诊断输出 schema：是否复用 DiagnosisResult
- 性能：P99<5s 的预算如何分配（LLM 3s + RAG 1s + 调度 1s）

## Step 2：阅读现有代码
- `app/agents/protocols.py`（AgentContext / AgentResponse）
- `app/agents/dispatcher.py`（specialist 注入方式）
- `app/agents/knowledge.py`（KnowledgeAgent 接口）
- `app/inference/factory.py`（create_inference_engine_from_settings）
- `app/models/schemas.py`（DiagnosisRequest / DiagnosisResult）

## Step 3：实现 `app/agents/specialist/base.py`

```python
from abc import abstractmethod
from app.agents.protocols import Agent, AgentContext, AgentResponse
from app.agents.knowledge import KnowledgeAgent
from app.inference.protocols import InferenceEngine
from app.models.schemas import Organ

class SpecialistAgent(Agent):
    def __init__(
        self,
        organ: Organ,
        inference_engine: InferenceEngine,
        knowledge_agent: KnowledgeAgent,
        prompt_template: str,
    ):
        self._organ = organ
        self._inference = inference_engine
        self._knowledge = knowledge_agent
        self._prompt_template = prompt_template
    
    @abstractmethod
    def get_organ(self) -> Organ: ...
    
    @abstractmethod
    def get_grading_system(self) -> str: ...  # "TI-RADS" / "LI-RADS" / "LVEF"
    
    async def invoke(self, context: AgentContext) -> AgentResponse:
        # 1. RAG 检索（5 条）
        chunks = await self._knowledge.query(
            query=context.user_query,
            organ=self._organ,
            top_k=5
        )
        # 2. 构造 prompt
        prompt = self._build_prompt(context, chunks)
        # 3. LLM 推理
        llm_response = await self._inference.generate(prompt=prompt, max_tokens=1024)
        # 4. 解析输出
        return self._parse_response(llm_response, chunks)
    
    def _build_prompt(self, context: AgentContext, chunks: List[KnowledgeChunk]) -> str:
        ...
    
    @abstractmethod
    def _parse_response(self, llm_response: str, chunks: List[KnowledgeChunk]) -> AgentResponse: ...
````

## Step 4：实现 `app/agents/specialist/thyroid.py`

### 关键要素：

- grading\_system: "TI-RADS" (TR1-TR5)
- 关键特征：成分、回声、形态、边缘、钙化
- 规则：每个特征映射到分数（来自 ACR TI-RADS 2017）
- LLM 提示词模板：包含 5 个 TI-RADS 等级的典型病例

### 系统提示词（`app/agents/prompts/thyroid.md`）：

```markdown
# 角色
你是超声诊断 AI 助手，专精甲状腺结节 TI-RADS 分级。

# 输入
- 影像特征（成分/回声/形态/边缘/钙化）
- 病灶大小
- 临床信息

# 输出
- TI-RADS 等级（TR1-TR5）
- 置信度（0-1）
- 证据来源（引用的知识库条目 ID）

# 工具
- RAG 检索：调用 get_ultrasound_knowledge(query, organ="thyroid", top_k=5)

# Few-shot 示例
[3 个 TR2/TR3/TR4 病例]

# 边界
- 若证据不足（top_k 结果 score < 0.5），返回 confidence=0.0 并标记 "需要人工复核"
- 若 LLM 输出格式不符，触发降级路径
```

## Step 5：实现 `app/agents/specialist/liver.py`

### 关键要素：

- grading\_system: "LI-RADS" (LR-1 \~ LR-5, LR-M, LR-TIV)
- 关键特征：病灶大小、动脉期高强化、廓清、包膜、"廓清+包膜"组合
- 系统提示词：包含 CT/MRI LI-RADS 算法的简化版

## Step 6：实现 `app/agents/specialist/cardiac.py`

### 关键要素：

- grading\_system: "LVEF" (正常 ≥55%、轻度降低 45-54%、中度 30-44%、重度 <30%)
- 关键特征：左室舒张末径、室壁厚度、瓣膜情况
- 系统提示词：包含 ASE 2015 心脏评估标准

## Step 7：注册到 Dispatcher

修改 `app/agents/dispatcher.py`：

```python
ORGAN_TO_SPECIALIST = {
    Organ.THYROID: "thyroid_specialist",
    Organ.LIVER: "liver_specialist",
    Organ.CARDIAC: "cardiac_specialist",
}

def build_dispatcher(inference_engine, knowledge_agent) -> DispatcherAgentImpl:
    specialists = {
        Organ.THYROID: ThyroidSpecialistAgent(inference_engine, knowledge_agent),
        Organ.LIVER: LiverSpecialistAgent(inference_engine, knowledge_agent),
        Organ.CARDIAC: CardiacSpecialistAgent(inference_engine, knowledge_agent),
    }
    return DispatcherAgentImpl(specialists)
```

## Step 8：编写测试

### `tests/unit/agt/test_thyroid_specialist.py`

- 测试 5 个 TI-RADS 等级识别
- 测试置信度计算
- 测试 RAG 检索结果融合
- Mock LLM 返回固定输出

### `tests/unit/agt/test_liver_specialist.py`

- 测试 LI-RADS 等级识别（LR-3/4/5 重点）
- 测试"廓清+包膜"特征组合

### `tests/unit/agt/test_cardiac_specialist.py`

- 测试 LVEF 计算
- 测试严重程度分级

### `tests/integration/agt/test_specialist_e2e.py`

- 端到端：3 个器官各 100 条测试样本
- 验证 P99 < 5s
- 验证分级准确率（与金标准对比）

## Step 9：验证

- `pytest tests/unit/agt tests/integration/agt -v` 全过
- 三器官并行测试（独立聊天窗口）后可合并
- 在 `app/main.py` lifespan 中调用 `build_dispatcher(...)` 并 `app.state.dispatcher = ...`

# 验收标准

- [ ] T-GA-AGT-04：甲状腺 TI-RADS 5 等级覆盖，准确率 ≥ 80%
- [ ] T-GA-AGT-05：肝脏 LI-RADS 核心等级（LR-3/4/5）覆盖，准确率 ≥ 75%
- [ ] T-GA-AGT-06：心脏 LVEF 4 档分级覆盖，准确率 ≥ 80%
- [ ] 单器官端到端 P99 < 5s（含 LLM + RAG + 调度）
- [ ] 单元测试覆盖率 ≥ 90%
- [ ] 三器官集成测试通过

# 提交规范

完成后：

1. `git commit -m "feat(AGT): T-GA-AGT-04/05/06 - 三专科智能体"`
2. 在 `archive_of_achivement/archive_phase0A_agt_specialists_{日期}/PROJECT_ARCHIVE.md` 写归档报告
3. 包含：分级准确率报告、P99 延迟报告、Few-shot 示例集

# 风险与依赖

- 风险：LLM 输出格式不稳定 → 使用 structured output（json schema 约束）
- 风险：单器官 P99 难达 5s → 优先确保 80% 请求 < 3s，允许 20% 长尾到 5s
- 后续依赖：完成后立即通知 FUS 轨道（融合层 FUS-01 依赖至少一个专科智能体）

***

## 10. 提示词 #6：FUS 融合层（T-GA-FUS-01/02）

> **Wave 5+6 启动** | 预计工作量：5-7 天 | 依赖：AGT-04 + RAG-03

***

### 10.1 提示词正文

```markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深医疗 AI 工程师，专精多源决策融合与一致性校验。

# 项目背景（必读）
- 任务：完成 T-GA-FUS-01（融合层核心：冲突检测+一致性校验+分层校验）+ T-GA-FUS-02（Self-RAG 质检+CRAG 补充检索集成）
- 验收：冲突检测准确率 100%、质检通过率 ≥ 80%
- 已有：`app/agents/dispatcher.py`（specialist_responses 聚合）
- 已有：`app/agents/specialist/`（三专科智能体）
- 已有：`app/fusion/desensitization.py`（融合层脱敏）
- 已有：`app/rag/service.py`（RAGService，含 retriever + reranker）

# 详细步骤

## Step 1：先思考（输出假设清单）
- 冲突类型：分级冲突、置信度冲突、建议冲突、证据冲突
- 一致性校验：与既往诊断对比（如果存在）、与临床指南对比
- 分层校验：先冲突检测 → 再一致性 → 再 Self-RAG
- Self-RAG 触发条件：置信度 < 阈值、跨器官不一致、证据不足
- CRAG 触发：Self-RAG 失败后的兜底

## Step 2：阅读现有代码
- `app/agents/dispatcher.py`（aggregate 方法）
- `app/agents/protocols.py`（AgentResponse）
- `app/fusion/desensitization.py`（融合层脱敏风格）
- `app/rag/service.py`（RAG Service 接口）

## Step 3：创建 `app/fusion/` 完整包结构

```

app/fusion/
├── __init__.py
├── protocols.py          # FusionEngine Protocol
├── conflict.py           # T-GA-FUS-01 冲突检测
├── consistency.py        # T-GA-FUS-01 一致性校验
├── layered\_validator.py  # T-GA-FUS-01 分层校验
├── self\_rag.py           # T-GA-FUS-02 Self-RAG
├── crag.py               # T-GA-FUS-02 CRAG
├── service.py            # 融合层编排器
└── desensitization.py    # ✅ 已有

````

## Step 4：实现 T-GA-FUS-01 `app/fusion/protocols.py`

```python
from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel
from app.agents.protocols import AgentResponse

class ConflictType(str, Enum):
    GRADING_CONFLICT = "grading_conflict"
    CONFIDENCE_CONFLICT = "confidence_conflict"
    RECOMMENDATION_CONFLICT = "recommendation_conflict"
    EVIDENCE_CONFLICT = "evidence_conflict"

class ConflictReport(BaseModel):
    has_conflict: bool
    conflict_types: List[ConflictType]
    conflicting_responses: List[AgentResponse]
    resolution_suggestion: str  # "keep_highest_confidence" / "require_human_review"

class ConsistencyCheckResult(BaseModel):
    is_consistent: bool
    inconsistent_organ_pairs: List[tuple[str, str]]  # [("thyroid", "liver")]
    severity: str  # "info" / "warning" / "critical"

class FusionRequest(BaseModel):
    agent_responses: List[AgentResponse]
    prior_diagnosis: Optional[dict] = None  # 既往诊断（如有）
    clinical_context: dict = {}

class FusionResult(BaseModel):
    final_response: AgentResponse
    conflict_report: ConflictReport
    consistency_result: ConsistencyCheckResult
    self_rag_passed: Optional[bool] = None
    crag_triggered: bool = False
    needs_review: bool = False
    review_reason: Optional[str] = None

class FusionEngine(ABC):
    @abstractmethod
    async def fuse(self, request: FusionRequest) -> FusionResult: ...
````

## Step 5：实现 T-GA-FUS-01 `app/fusion/conflict.py`

### 步骤：

1. GRADING\_CONFLICT：同器官不同专科给出不同分级（如甲状腺 TR3 vs TR4）
2. CONFIDENCE\_CONFLICT：两个 response confidence 差距 > 0.4
3. RECOMMENDATION\_CONFLICT：建议冲突（活检 vs 随访）
4. EVIDENCE\_CONFLICT：引用的知识库条目互相矛盾

### 关键代码：

```python
class ConflictDetector:
    def detect(self, responses: List[AgentResponse]) -> ConflictReport:
        conflicts = []
        # 同器官冲突
        organ_groups = group_by_organ(responses)
        for organ, group in organ_groups.items():
            if len(set(r.content for r in group)) > 1:
                conflicts.append(ConflictType.GRADING_CONFLICT)
        # 置信度冲突
        confidences = [r.confidence for r in responses]
        if max(confidences) - min(confidences) > 0.4:
            conflicts.append(ConflictType.CONFIDENCE_CONFLICT)
        # ... 其他冲突
        return ConflictReport(
            has_conflict=bool(conflicts),
            conflict_types=conflicts,
            ...
        )
```

## Step 6：实现 T-GA-FUS-01 `app/fusion/consistency.py`

### 步骤：

1. 与既往诊断对比：若 prior\_diagnosis 存在，校验当前结果与既往结果的一致性
2. 与临床指南对比：复用 RAG Service 检索当前器官的临床指南
3. 跨器官一致性：检查不同器官的诊断结果是否互斥（如：心脏 LVEF<30% 与甲状腺 TR2 通常不互斥，但需记录）

## Step 7：实现 T-GA-FUS-01 `app/fusion/layered_validator.py`

### 三层校验：

- Layer 1：conflict 检测 → 通过则进入 Layer 2，否则标记 needs\_review
- Layer 2：consistency 校验 → 通过则进入 Layer 3
- Layer 3：Self-RAG 质检（如果启用）

## Step 8：实现 T-GA-FUS-02 `app/fusion/self_rag.py`

### Self-RAG 流程：

1. 接收 AgentResponse
2. 构造 self-evaluation prompt：
   - "你的诊断是否有充分证据支持？（Y/N）"
   - "证据是否与诊断结论一致？（1-5 分）"
   - "是否有遗漏的关键特征？（列出）"
3. 调用 LLM 自我评估
4. 解析输出，决定：pass / retry / needs\_review
5. 验收：质检通过率 ≥ 80%

## Step 9：实现 T-GA-FUS-02 `app/fusion/crag.py`

### CRAG 流程：

1. Self-RAG 失败后触发
2. 评估失败原因（证据不足 / 证据矛盾 / 证据不相关）
3. 重新构造查询：基于原 query + 失败原因
4. 调用 RAG Service 的 retriever 重新检索（增加 top\_k 到 20）
5. 用新证据重新调用 specialist
6. 若仍失败，标记 needs\_review

## Step 10：实现 `app/fusion/service.py`

```python
class FusionEngineImpl(FusionEngine):
    def __init__(
        self,
        conflict_detector: ConflictDetector,
        consistency_checker: ConsistencyChecker,
        self_rag_evaluator: SelfRAGEvaluator,
        crag_handler: CRAGHandler,
        inference_engine: InferenceEngine,
    ):
        ...
    
    async def fuse(self, request: FusionRequest) -> FusionResult:
        # Layer 1: 冲突检测
        conflict = self._conflict_detector.detect(request.agent_responses)
        if conflict.has_conflict:
            return FusionResult(needs_review=True, review_reason="conflict_detected", ...)
        
        # Layer 2: 一致性校验
        consistency = self._consistency_checker.check(request)
        if not consistency.is_consistent and consistency.severity == "critical":
            return FusionResult(needs_review=True, review_reason="consistency_failed", ...)
        
        # Layer 3: Self-RAG
        final_response = self._select_final_response(request.agent_responses)
        self_rag_result = await self._self_rag.evaluate(final_response)
        if not self_rag_result.passed:
            # CRAG
            crag_result = await self._crag.handle(final_response, self_rag_result)
            if crag_result.recovered:
                final_response = crag_result.new_response
            else:
                return FusionResult(needs_review=True, review_reason="self_rag_failed", ...)
        
        return FusionResult(final_response=final_response, ...)
```

## Step 11：编写测试

### `tests/unit/fus/test_conflict.py`

- 4 种冲突类型各 5 个用例
- 冲突检测准确率 100%

### `tests/unit/fus/test_consistency.py`

- 既往诊断对比
- 跨器官一致性

### `tests/unit/fus/test_self_rag.py`

- 通过/失败场景各 5 个
- 质检通过率 ≥ 80%

### `tests/integration/fus/test_fusion_e2e.py`

- 端到端融合测试
- 验证 needs\_review 标记正确

## Step 12：验证

- `pytest tests/unit/fus tests/integration/fus -v` 全过
- 在 `app/main.py` lifespan 中实例化 `app.state.fusion_engine = FusionEngineImpl(...)`

# 验收标准

- [ ] T-GA-FUS-01：冲突检测 4 种类型全覆盖，准确率 100%
- [ ] T-GA-FUS-01：一致性校验规则执行正确
- [ ] T-GA-FUS-02：Self-RAG 质检通过率 ≥ 80%
- [ ] T-GA-FUS-02：CRAG 触发条件正确，恢复率 ≥ 50%
- [ ] 融合 P99 延迟 < 500ms（不含 Self-RAG LLM 调用）
- [ ] 单元测试覆盖率 ≥ 90%

# 提交规范

完成后：

1. `git commit -m "feat(FUS): T-GA-FUS-01/02 - 融合层核心+Self-RAG+CRAG"`
2. 在 `archive_of_achivement/archive_phase0A_fus_{日期}/PROJECT_ARCHIVE.md` 写归档报告
3. 包含：冲突检测准确率、Self-RAG 通过率、CRAG 恢复率报告

# 风险与依赖

- 风险：Self-RAG 增加延迟 → 仅在低置信度时触发
- 后续依赖：完成后通知 INT 轨道（T-GA-INT-01 端到端集成依赖 FUS-02）

***

## 11. 提示词 #7：LLM 选型报告（T-GA-LLM-04）

> **Wave 1 启动**（独立轨道） | 预计工作量：3-5 天 | 依赖：T-G0-LLM-01 ✅

***

### 11.1 提示词正文

````markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深 NLP/ML 工程师，专精 LLM 推理优化与中文医疗问答。

# 项目背景（必读）
- 任务：完成 T-GA-LLM-04：LLM 基座模型选型对比
- 验收：
  1. 中文医疗问答准确率 ≥ 85%（自建超声术语测试集 ≥ 500 条）
  2. RTX 3060 单次推理 < 3s
  3. 量化后幻觉率增量 < 0.5%
- 候选模型：Qwen2.5-7B / ChatGLM4-9B / InternLM2.5-7B（三选一）
- 已有：`app/inference/llama_cpp.py`（已实现）
- 已有：`app/inference/vllm.py`（已实现）
- 已有：`app/inference/benchmark.py`（基准测试框架）
- 已有：`deploy/Qwen2.5-1.5B-Instruct/`（1.5B 模型已下载）

# 详细步骤

## Step 1：先思考（输出假设清单）
- 三个候选模型的对比维度：参数量、上下文长度、中文能力、医疗领域表现
- 测试集构建：自建 vs 公开（CMB、CMMLU、MedQA）
- 评测指标：BLEU / ROUGE / LLM-as-Judge / 人工盲评
- 量化方案：Q4_K_M（已有）/ Q5_K_M / Q8_0
- 硬件约束：RTX 3060 (12GB) 单卡

## Step 2：阅读现有代码
- `app/inference/llama_cpp.py`（InferenceEngine 实现）
- `app/inference/vllm.py`
- `app/inference/benchmark.py`（基准测试 API）
- `app/inference/hallucination_detector.py`（幻觉检测）
- `app/inference/grading_definitions.py`（分级定义）

## Step 3：构建测试集 `data/eval/ultrasound_qa_500.jsonl`

### 测试集内容：
- 500 条超声术语相关问答
- 覆盖：甲状腺（150）/ 肝脏（150）/ 心脏（200）
- 题型：分级判断（200）、特征识别（200）、建议生成（100）
- 每条格式：
  ```json
  {"question": "...", "reference_answer": "...", "organ": "thyroid", "grading": "TR3"}
````

## Step 4：实现 `scripts/benchmark_llm_candidates.py`

### 步骤：

1. 接受参数：`--models`（逗号分隔的模型路径）、`--test-set`、`--quantization`
2. 对每个模型：
   - 加载（使用 llama.cpp）
   - 推理 500 条测试集
   - 计算指标：准确率、BLEU、ROUGE-L、延迟 P50/P95/P99、显存占用
3. 调用 LLM-as-Judge（用 Qwen2.5-7B 自身作为 judge）评估答案质量
4. 调用 `app/inference/hallucination_detector.py` 评估幻觉率
5. 输出对比报告 `data/eval/benchmark_report_{date}.md`

## Step 5：实施三轮基准测试

### Round 1：原始模型（FP16）

- 候选 1：Qwen2.5-7B-Instruct
- 候选 2：ChatGLM4-9B
- 候选 3：InternLM2.5-7B-Chat

### Round 2：Q4\_K\_M 量化

- 同三个模型，量化到 Q4\_K\_M

### Round 3：Q5\_K\_M 量化（仅最佳模型）

- 在 Round 2 中表现最佳的模型，量化到 Q5\_K\_M

## Step 6：编写选型报告 `docs-for-guiding generating docs/开发计划/LLM选型报告-{日期}.md`

### 报告结构：

1. 候选模型概览
2. 测试方法（500 条测试集 + 评测指标）
3. 原始模型对比（准确率/延迟/显存）
4. 量化模型对比（Q4\_K\_M / Q5\_K\_M）
5. 幻觉率对比
6. **选型决策**（推荐哪个模型 + 理由）
7. 风险与回退方案

## Step 7：更新 `app/config/settings.py`

根据选型结果，更新默认配置：

```python
# LLM 推理引擎配置
llm_model_name: str = "Qwen2.5-7B-Instruct-Q4_K_M"  # 选型结果
llm_model_path: str = "/models/qwen2.5-7b-instruct-q4_k_m.gguf"
llm_context_length: int = 4096
llm_default_max_tokens: int = 1024
```

## Step 8：部署选定的模型

- 下载选定模型的 GGUF 量化版本
- 保存到 `deploy/` 目录
- 更新 `docker-compose.llm.yml` 中的模型路径

## Step 9：编写测试 `tests/integration/llm/test_selected_model.py`

- 验证选型模型在 500 条测试集上的准确率 ≥ 85%
- 验证单次推理 < 3s
- 验证幻觉率 < 5%（量化后增量 < 0.5%）

## Step 10：验证

- 三个候选模型均能成功推理
- 选型报告完整，对比维度清晰
- 推荐模型满足所有验收标准
- 部署文件可被 `app/main.py` lifespan 加载

# 验收标准

- [ ] T-GA-LLM-04：500 条测试集构建完成
- [ ] T-GA-LLM-04：三个候选模型均完成基准测试
- [ ] T-GA-LLM-04：推荐模型准确率 ≥ 85%
- [ ] T-GA-LLM-04：推荐模型单次推理 < 3s
- [ ] T-GA-LLM-04：量化后幻觉率增量 < 0.5%
- [ ] 选型报告完整
- [ ] `app/config/settings.py` 更新默认配置
- [ ] 部署文件就位

# 提交规范

完成后：

1. `git commit -m "feat(LLM): T-GA-LLM-04 - 基座模型选型报告（推荐X）"`
2. 在 `archive_of_achivement/archive_phase0A_llm_selection_{日期}/PROJECT_ARCHIVE.md` 写归档报告
3. 包含：完整选型报告路径、对比表格、最终决策

# 风险与依赖

- 风险：三个模型都达不到 85% → 评估切换到 13B 量化版本
- 风险：RTX 3060 显存不足 → 使用 CPU offload 或 4bit 量化
- 后续依赖：完成后通知所有 AGT 轨道（专科智能体使用选定的 LLM）

***

## 12. 提示词 #8：INT 端到端集成（T-GA-INT-01）

> **Wave 7 启动**（最后） | 预计工作量：3-5 天 | 依赖：FUS-02 + SG-02 ✅ + API-02 ✅

***

### 12.1 提示词正文

````markdown
# 角色定位
你是超声诊疗决策支持 AI 系统的资深全栈架构师，专精端到端集成与性能调优。

# 项目背景（必读）
- 任务：完成 T-GA-INT-01：核心推理链路端到端集成 + 延迟验证
- 验收：单器官 P99 < 5s（不含重试）/ P99 < 8s（含 Self-RAG 重试）
- 已有：
  - `app/main.py`（FastAPI lifespan 已就绪）
  - `app/api/v1/diagnosis.py`（诊断接口已实现）
  - `app/agents/dispatcher.py`、`specialist/`（智能体系统）
  - `app/rag/service.py`（RAG 管线）
  - `app/fusion/service.py`（融合层）
  - `app/services/safety_gateway_client.py`（SafetyGateway 客户端）
  - `safety_gateway/main.py`（独立进程）

# 详细步骤

## Step 1：先思考（输出假设清单）
- 端到端链路：API 接收 → 调度 → 知识查询 → 专科推理 → 融合层 → SafetyGateway → API 返回
- 延迟预算分配：API 网关 100ms / 调度 50ms / RAG 1000ms / LLM 3000ms / 融合 500ms / SG 50ms / 总 4700ms
- 失败处理：每个环节的降级策略
- 重试机制：仅在 LLM 失败时重试（最多 1 次）
- 监控埋点：每环节的 latency 写入 structlog

## Step 2：阅读现有代码
- `app/main.py`（lifespan 完整阅读）
- `app/api/v1/diagnosis.py`（诊断接口完整阅读）
- `app/agents/dispatcher.py`（build_dispatcher 函数）
- `app/services/safety_gateway_client.py`（warmup + start_health_probe）
- `app/config/dependencies.py`（依赖注入）

## Step 3：在 `app/main.py` lifespan 中串联完整链路

修改 `app/main.py` 的 lifespan 函数，添加：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 现有初始化 ...
    
    # 初始化智能体系统
    from app.agents.knowledge import KnowledgeAgentImpl
    from app.agents.dispatcher import build_dispatcher
    from app.agents.monitor import MonitorAgentImpl
    from app.fusion.service import FusionEngineImpl
    from app.rag.service import RAGServiceImpl
    
    rag_service = RAGServiceImpl(...)
    knowledge_agent = KnowledgeAgentImpl(rag_service, await RedisPool.get_client())
    monitor_agent = MonitorAgentImpl(...)
    dispatcher = build_dispatcher(
        inference_engine=inference_engine,
        knowledge_agent=knowledge_agent,
    )
    fusion_engine = FusionEngineImpl(...)
    
    app.state.rag_service = rag_service
    app.state.knowledge_agent = knowledge_agent
    app.state.monitor_agent = monitor_agent
    app.state.dispatcher = dispatcher
    app.state.fusion_engine = fusion_engine
    
    # ... 现有 yield 逻辑 ...
````

## Step 4：在 `app/api/v1/diagnosis.py` 中实现端到端调用

修改诊断接口的 `submit_diagnosis` 端点：

```python
@router.post("/request", response_model=DiagnosisRequestResponse)
async def submit_diagnosis(
    request: DiagnosisRequest,
    db: AsyncSession = Depends(get_db_session),
    ...
):
    # 1. 同意状态校验
    consent_ok = await check_consent_status(db, request.patient_id, request.organ)
    if not consent_ok:
        raise PatientConsentWithdrawnException(...)
    
    # 2. 调度智能体
    agent_context = AgentContext(
        request_id=request.request_id,
        session_id=request.session_id,
        organ=request.organ,
        user_query=request.user_query,
    )
    
    # 3. 调用所有相关专科智能体
    specialist_responses = await app.state.dispatcher.invoke_all(agent_context)
    
    # 4. 融合层处理
    fusion_result = await app.state.fusion_engine.fuse(
        FusionRequest(agent_responses=specialist_responses)
    )
    
    # 5. SafetyGateway 校验
    sg_result = await app.state.safety_gateway_client.check(
        diagnosis_result=fusion_result.final_response,
        patient_id=request.patient_id,
    )
    
    if not sg_result.passed:
        raise SafetyGatewayUnavailableException(...)
    
    # 6. 流程监控埋点
    await app.state.monitor_agent.track_diagnosis(request, fusion_result)
    
    # 7. 返回结果
    return DiagnosisRequestResponse(
        request_id=request.request_id,
        status="completed",
        result=fusion_result.final_response,
    )
```

## Step 5：实现每环节延迟埋点

在每个环节用 structlog 记录 latency：

```python
import time

async def measure_latency(name, coro):
    start = time.perf_counter()
    result = await coro
    latency_ms = (time.perf_counter() - start) * 1000
    structlog.get_logger().info(
        f"diagnosis.{name}.latency_ms",
        latency_ms=latency_ms,
        request_id=context.request_id,
    )
    return result
```

## Step 6：实现失败降级

每个环节失败时的降级策略：

| 环节            | 失败处理                               |
| ------------- | ---------------------------------- |
| 调度            | 返回错误 E5001                         |
| 知识查询          | RAG Service 降级到 BM25               |
| 专科推理          | LLM 失败时重试 1 次，仍失败则返回 needs\_review |
| 融合层           | 冲突时返回 needs\_review                |
| SafetyGateway | SG 不可用时返回 E4005，标记诊断为待复核           |

## Step 7：编写端到端测试 `tests/integration/int/test_e2e_diagnosis.py`

### 测试用例：

1. **正常路径**（甲状腺 TR3 病例）
2. **多器官并行**（甲状腺 + 肝脏）
3. **LLM 重试**（mock LLM 第一次失败）
4. **RAG 降级**（Qdrant 不可用 → BM25）
5. **SafetyGateway 拒绝**（禁忌触发）
6. **同意撤回**（SafetyGateway 直查 PG）

每个用例：

- 验证响应状态码
- 验证响应字段
- 验证延迟 < 5s（P99）

## Step 8：编写延迟基准测试 `tests/integration/int/test_e2e_latency.py`

```python
import pytest
import time

@pytest.mark.asyncio
async def test_single_organ_p99_latency():
    """验证单器官端到端 P99 < 5s（不含重试）"""
    latencies = []
    for _ in range(100):
        start = time.perf_counter()
        response = await submit_diagnosis(test_request_thyroid_tr3)
        latency = time.perf_counter() - start
        latencies.append(latency)
    
    p99 = sorted(latencies)[98]  # 第 99 百分位
    assert p99 < 5.0, f"P99 latency {p99}s exceeds 5s"
```

## Step 9：性能调优（如未达标）

如果 P99 > 5s，按以下顺序优化：

1. **RAG 检索**：缓存命中率提升 → 降低 P99
2. **LLM 推理**：使用 vLLM 而非 llama.cpp（更高吞吐）
3. **融合层**：Self-RAG 异步执行
4. **SafetyGateway**：熔断器保护（已实现，确认生效）

## Step 10：验证

- `pytest tests/integration/int -v` 全过
- 100 次端到端测试，P99 < 5s
- 20 次包含 Self-RAG 重试的测试，P99 < 8s
- 启动 `app/main.py`，调用 `POST /api/v1/diagnosis/request` 成功

# 验收标准

- [ ] 端到端链路完整：API → 调度 → RAG → 专科 → 融合 → SG → API
- [ ] 单器官 P99 < 5s（不含重试）
- [ ] 单器官 P99 < 8s（含 Self-RAG 重试）
- [ ] 失败降级：6 种场景全部正确处理
- [ ] 延迟埋点完整（每环节 < 1ms 开销）
- [ ] 端到端测试 100% 通过

# 提交规范

完成后：

1. `git commit -m "feat(INT): T-GA-INT-01 - 端到端集成+P99<5s"`
2. 在 `archive_of_achivement/archive_phase0A_integration_{日期}/PROJECT_ARCHIVE.md` 写归档报告
3. 包含：P50/P95/P99 延迟分布、降级场景验证、性能调优记录

# 风险与依赖

- 风险：P99 难达 5s → 优先保证 80% 请求 < 3s
- 风险：SafetyGateway 不可用导致请求全失败 → 实施熔断保护
- Gate 0A 通过：本任务完成即可标记 Gate 0A 为 ✅

```

---

## 13. 提示词使用与执行流程

### 13.1 启动顺序

```

Day 1-3（Wave 1 同时启动 4 个聊天窗口）：

- 聊天窗口 A：使用「提示词 #1：DATA 轨道」
- 聊天窗口 B：使用「提示词 #2：EMB 轨道」
- 聊天窗口 C：使用「提示词 #7：LLM 选型」（独立任务）
- 聊天窗口 D：使用「提示词 #4：AGT 基础智能体」

Day 4-7（Wave 2 启动 4 个聊天窗口）：

- 聊天窗口 E：使用「提示词 #3：RAG 轨道」
- 聊天窗口 F：使用「提示词 #4（继续）」（AGT-02/03/07）
- 聊天窗口 G：使用「提示词 #2（继续）」（EMB-02 Qdrant）
- 聊天窗口 H：使用「提示词 #5：AGT 专科智能体」（等待 RAG-03）

Day 8-14（Wave 3-5）：

- RAG-03 + AGT 三专科 + FUS-01

Day 15-17（Wave 6）：

- FUS-02

Day 18-20（Wave 7）：

- INT 端到端集成
- 触发 Gate 0A 评审

```

### 13.2 协调机制

- **每日站会**：每个聊天窗口的开发者汇报进度
- **接口变更通知**：通过 Redis Pub/Sub 广播接口变更
- **集成日**：每周五执行集成测试
- **接口锁定**：Protocol 评审通过后纳入 Git 控制

### 13.3 完成 Gate 0A 评审

当以下任务全部完成时，触发 Gate 0A 评审：
- [x] T-GA-INT-01 端到端集成通过
- [x] 单器官 P99 < 5s
- [x] 内存预算验证（compact 模式 ≤ 22GB）
- [x] 显存碎片化率 < 30%
- [x] llama.cpp / vLLM 一致性 ≥ 95%
- [x] LLM 基座模型选型完成

---

## 14. 风险与回退方案

| 风险 | 影响 | 回退方案 |
|------|------|---------|
| AGT 智能体延迟超 5s | INT 端到端 P99 难达 | 优先 LLM 推理优化（vLLM、量化） |
| RAG 召回率不达标 | 专科诊断准确率低 | 增加 BM25 权重；考虑使用 bge-m3-large |
| Self-RAG 通过率 < 80% | 重试次数增加 | 调整 self-evaluation prompt |
| LLM 选型失败（< 85%） | 所有 AGT 阻塞 | 切换到 13B 量化或外接 API |
| Qdrant 部署失败 | EMB 阻塞 | 使用 PGVector 兜底（已实现） |

---

## 15. 文档维护

完成 Phase 0A 后：
1. 更新 `开发计划.md` 中所有 Phase 0A 任务状态为 ✅
2. 在 `archive_of_achivement/` 创建 `archive_phase0A_complete_{日期}/` 目录
3. 撰写 `archive_phase0A_complete_{日期}/GATE_0A_REVIEW.md`（Gate 评审报告）
4. 通知项目所有 stakeholder

---

**版本**：v1.0 | **最后更新**：2026-06-29 | **下次评审**：Gate 0A 通过后
```

