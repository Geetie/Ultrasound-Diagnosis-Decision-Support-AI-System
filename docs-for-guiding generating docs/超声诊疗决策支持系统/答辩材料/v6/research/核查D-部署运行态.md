# 核查 D — 部署与运行态

> **范围**：这套系统实际怎么部署、有几个服务、每个服务的真实端口/模型/状态。
> **取证口径**：只写能在仓库里指到 `path:line` 的事实。查不到的写"查不到"，不推断。
> **取证时间**：2026-10-28 · 工作目录 `/Users/silver/Dsh/UDDS-Ultrasound`
> **纪律**：本文只覆盖部署与运行态，不涉及前端业务逻辑、评测指标、知识库内容。

---

## 0. 一句话结论

**代码里能跑的服务是 9 个进程/容器，但当前这台 Mac 上一个都没跑（零端口监听、无 `.env`）。**
**有 3 个布尔开关是彻底的死配置（全库零消费），另有 1 个端口字段（`api_port=8000`）是"定义了但不是绑定来源"的假真值——真机实际在 18000。**

最容易被答辩追问翻车的三件事：

| # | 事实 | 危险点 |
|---|---|---|
| 1 | 主服务真机端口是 **18000**，不是代码默认的 **8000** | 说"8000 端口"会被 SSH 隧道配置反证 |
| 2 | 判官 **8006** 在 `settings.py` 里**完全不存在**（0 次出现） | 它只活在 shell 脚本里，是"脚本级服务"而非"配置级服务" |
| 3 | Qdrant 真机跑的是**二进制**，不是 docker 容器 | 说"Qdrant 容器"与真机记录不符 |

---

## 1. 服务清单（逐个端口核实）

### 1.1 总表

| 服务 | 端口 | 形态 | 模型/内容 | 来源 |
|---|---|---|---|---|
| 主服务（FastAPI） | **18000**（裸机）／8000（容器） | uvicorn 单进程 + 协程池 | — | `scripts/start_main.sh:35`、`Dockerfile.dev:29` |
| SafetyGateway | **9001** | **独立进程**（uvicorn，非同一进程） | 规则引擎 + NER | `scripts/server_start_safety_gateway.sh:22`、`deploy/supervisor/safety-gateway.conf:9` |
| PostgreSQL | **5432** | docker 容器 `ankane/pgvector:latest` | pgvector 扩展 | `docker-compose.dev.yml:3,13` |
| Redis | **6379** | docker 容器 `redis:7.2-alpine` | 队列 + 缓存 + 脱敏 HMAC | `docker-compose.dev.yml:33,37` |
| Qdrant | **6333**（REST）／6334（gRPC） | 真机为**二进制**，非容器 | 向量索引 3269 点 | `app/config/settings.py:386-387`；二进制事实见 21 号 E6 |
| vLLM 主生成 | **8002** | 独立 venv 进程 | `Qwen2.5-1.5B-Instruct` | `scripts/server_start_vllm_engine.sh:41` |
| llama.cpp 引擎 | **8001** | docker 容器 | GGUF `qwen2.5-1.5b-instruct-q4_k_m` | `docker-compose.llm.yml:21` |
| 图像嵌入 | **8003** | 独立进程（GPU） | **BiomedCLIP**（open_clip，512d，MIT） | `scripts/server_start_ultrasound_clip.sh:15,22` |
| 文本嵌入 | **8004** | 独立进程（GPU） | **bge-m3** | `scripts/server_start_embedding_services.sh:24` |
| 精排 | **8005** | 独立进程（GPU） | **bge-reranker-base**（278M） | `scripts/server_start_embedding_services.sh:30` |
| 判官（LLM-as-judge） | **8006** | 独立进程（GPU） | **Qwen3-8B**（GGUF Q4_K_M） | `scripts/server_start_judge.sh:8` |
| 边缘端 | **11112**（DICOM SCP） | docker 容器 / 真机 Jetson | TensorRT 导航（缺失退回空后端） | `docker-compose.dev.yml:162,181` |
| VictoriaMetrics | **8428** | docker 容器 | 指标存储 | `docker-compose.dev.yml:204` |
| Alertmanager | **9093** | docker 容器 | 告警 | `docker-compose.dev.yml:223` |
| vmbackup | 无端口 | docker 容器 | 每小时快照 | `docker-compose.dev.yml:241` |

**计数口径**：以上 **15 个条目**。若只算"本项目自己写的服务"（不含中间件与监控），是 **7 个**：主服务、SafetyGateway、vLLM(8002)、嵌入×2(8003/8004)、精排(8005)、判官(8006)。若把 8001 llama.cpp 也算上（compose 里有、真机作为 compact 档保留），是 8 个。

### 1.2 逐个核实（含关键差异）

#### 主服务：18000 而非 8000 ⚠️

```python
# app/config/settings.py:28-29
api_host: str = '127.0.0.1'
api_port: int = 8000
```

但同一段上方注释明确写（`settings.py:17-26`）：

> ⚠️ 本字段**不是** uvicorn 实际绑定的来源。真实绑定来自命令行 `--host`：
> · 容器：`Dockerfile.dev` 硬编码 `--host 0.0.0.0`（容器内必须…）
> · 裸机：一律走 `scripts/start_main.sh`

而 `scripts/start_main.sh:35`：

```bash
PORT="${API_PORT:-18000}"
```

`scripts/server_restart_all.sh:8-11` 更直接点明：

> 0) 融合层（主服务）在本部署里是 18000，不是默认 8000：
>    SG 的 fusion probe 默认探 localhost:8000 → 会一直 connection_refused。

**结论**：`api_port=8000` 是**可读真值，不是生效真值**。答辩若提端口，裸机应说 18000，跨机访问走 `ssh -N -L 8000:127.0.0.1:18000`。

#### 进程模型：单 uvicorn 进程 + 进程内 asyncio 协程池

```yaml
# Dockerfile.dev:29
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

`--workers 1` → **单进程**。worker "池"不是多进程：

```python
# app/services/diagnosis_worker.py:7-8
:func:`start_worker_pool` ：lifespan 启动期调用，启动 N 个 worker 协程，
每个 worker BLPOP 队列并通过 run_diagnosis_pipeline 执行完整诊断管线。
```

```python
# app/config/settings.py:267
int_worker_pool_size: int = 4
```

**注意一处文档不一致**：`settings.py:275` 的注释写"worker 16 位、单例 e2e ≈ 8s 量级"，而实际字段值是 **4**。注释里的 16 是旧标定值，代码为准 → **4 个协程槽位**。

#### SafetyGateway：确实是独立进程

不是同进程中间件，有三重证据：

1. `deploy/supervisor/safety-gateway.conf:9` — `command=uvicorn safety_gateway.main:app --host 0.0.0.0 --port 9001 --workers 1`
2. `scripts/server_start_safety_gateway.sh:22` — `nohup ... -m uvicorn safety_gateway.main:app --host 127.0.0.1 --port 9001`
3. `docker-compose.dev.yml:119,141` — 独立容器 `ultrasound-safety-gateway`，`ports: 9001:9001`

容器里还带 `oom_score_adj: -1000`（`docker-compose.dev.yml:141` 附近）——**禁止被 OOM 杀死**，这是安全关键服务的定位证据。

#### 判官 8006：配置里根本没有 ⚠️

```bash
grep -c "8006" app/config/settings.py
# → 0
```

8006 只出现在 `scripts/server_start_judge.sh:8`：

```bash
MODEL="$UAI/models/qwen3-8b-gguf/Qwen3-8B-Q4_K_M.gguf"
```

以及评测脚本的 `--judge-url http://127.0.0.1:8006 --model Qwen3-8B`（见 `session-handoff.md:402,424`）。

**结论**：8006 是**脚本级服务**，不在配置体系内。这既说明它"与诊断双引擎隔离"（`server_start_judge.sh:3` 注释原话），也说明它不是一个受配置管理的一等公民。

#### Qdrant：真机是二进制，不是容器

`docs-for-guiding generating docs/超声诊疗决策系统/21-远期路线图-20261009.md:51`：

> qdrant v1.12.4 二进制（gh-proxy 通道，docker registry 校园网全断）127.0.0.1:6333；pgvector→qdrant 迁移 3269 点

而设计文档 `01-系统架构设计.md:1056` 写的是"Docker 容器"。**设计态与真机态不一致**，真机用二进制。原因是校园网拉不到 docker image。

#### 端口绑定的安全纪律

`tests/unit/obs/test_metrics_bind_loopback.py:10` 记录了纪律：

> 其余端口（18000 API / 9001 SG / 8001-8005 模型服务）都规规矩矩绑在 `127.0.0.1`

实际绑定：

| 服务 | 绑定地址 | 来源 |
|---|---|---|
| 主服务（裸机） | `127.0.0.1` | `start_main.sh` / `server_restart_api.sh` |
| 主服务（容器） | `0.0.0.0`（容器内必须） | `Dockerfile.dev:29` |
| SG（裸机） | `127.0.0.1` | `server_start_safety_gateway.sh:22` |
| SG（容器） | `0.0.0.0`（容器内） | `supervisor/safety-gateway.conf:9` |
| 8002 vLLM | `127.0.0.1` | `server_start_vllm_engine.sh:41` |
| 8003/8004/8005 | `127.0.0.1` | `server_start_embedding_services.sh:2` 明写"只绑 127.0.0.1" |
| 8006 判官 | `127.0.0.1` | `server_start_judge.sh:20` |
| 指标 9090 | `127.0.0.1` | `settings.py:606` `obs_metrics_port` + `:612` `obs_metrics_host` |

**例外**：`scripts/server_restart_api.sh` 的注释与 `scripts/start_main_18000.sh:9` 用的是 `--host 0.0.0.0`。后者是历史遗留脚本，`start_main.sh` 才是官方入口（该文件自述"唯一权威的裸机主服务启动脚本"，并说明历史上 7 个 `deploy_*.sh` 各写 `0.0.0.0` 导致铁律被违反）。

---

## 2. 配置项审计

### 2.1 规模

`app/config/settings.py` **886 行**，其中布尔字段 **38 个**。

### 2.2 假开关（全库零消费）

用 `app/ edge/ safety_gateway/ scripts/` 全量 Python 源码做引用扫描，并额外检查 `getattr(settings, ...)` / `vars(settings)` / `model_dump()` 等动态访问路径。结果：

| 字段 | 默认 | 定义行 | 消费点 | 判定 |
|---|---|---|---|---|
| `rag_use_bm25_fallback` | `True` | `settings.py:395` | **0** | ✗ 假开关 |
| `rag_use_redis_cache` | `False` | `settings.py:446` | **0** | ✗ 假开关 |
| `obs_cache_monitor_enabled` | `True` | `settings.py:634` | **0** | ✗ 假开关 |

三条都只有定义行，连 `getattr` 动态访问都没有。验证命令：

```bash
grep -rn "rag_use_bm25_fallback" --include="*.py" . | grep -v .git
# → 只有 app/config/settings.py:395
```

**这次不是我先发现的——项目自己的台账已经记了其中一条**：

```markdown
# docs-for-guiding generating docs/超声诊疗决策支持系统/00b-统一配置参考手册.md:637
| `obs_cache_monitor_enabled` | `True` | 缓存监控（`start_prometheus_sync` 尚无调用方） |
```

```markdown
# docs-for-guiding generating docs/超声诊疗决策支持系统/05-可观测性与运维.md:475
| 🟡 | `cache_monitor.start_prometheus_sync` 无调用方（`obs_cache_monitor_enabled=True`） | `cache_monitor/monitor.py:312` |
```

即：`obs_cache_monitor_enabled` 是**项目自己标了黄灯的假开关**（括号里直接写"尚无调用方"）。另两个（`rag_use_bm25_fallback` / `rag_use_redis_cache`）只在设计文档 `.trae/documents/` 与手册里被列为"开关"，没有任何一期实现落地它们。

**它们在别处被"硬编码顶替"了**：

- **BM25**：真实开关是**请求级参数** `request.use_bm25_fallback`，见 `app/rag/service.py:291`：
  ```python
  if request.use_bm25_fallback and self._bm25_fallback.should_trigger(result):
  ```
  即：settings 里那个默认 `True` 的全局开关**完全无效**，每次检索是否走 BM25 由调用方传参决定。

- **`rag_cache_enabled` 才是真开关**（`app/main.py:505`），`rag_use_redis_cache` 是它的幽灵副本。

### 2.3 真开关（打开会真的改变行为）

抽查几个高影响开关，都确认有真实消费点：

| 字段 | 默认 | 消费点 | 打开后行为 |
|---|---|---|---|
| `rag_use_multimodal` | `False` | 3 处 | 启用影像检索腿 |
| `graph_query_enabled` | `False` | 4 处（含 `retriever.py:460`） | 图查询腿 |
| `rag_cache_enabled` | `True` | `main.py:505` | 检索缓存实例化 |
| `nodule_segmentation_enabled` | `True` | 1 处 | 结节分割 |
| `t2a_classifier_in_pipeline_enabled` | `False` | 2 处 | T2a 分类旁路 |
| `hallucination_detection_in_pipeline` | `False` | 1 处 | 幻觉检测进主链 |
| `multi_organ_parallel_dispatch_enabled` | `False` | 1 处 | 多器官并行派发 |
| `obs_metrics_server_enabled` | `True` | 1 处 | 启 `/metrics` 服务 |
| `obs_vm_push_enabled` | `True` | 1 处 | 推 VictoriaMetrics |

**一组值得注意的"默认关"**：`rag_use_multimodal`、`graph_query_enabled`、`t2a_classifier_in_pipeline_enabled`、`hallucination_detection_in_pipeline`、`multi_organ_parallel_dispatch_enabled` 全部默认 `False`。这与 `progress.md` 记录的"打开也没用/NO_GAIN"结论一致——**默认关不是遗漏，是实测后的选择**。

### 2.4 孤儿配置的自我记录

`settings.py` 里有大量"已删除孤儿配置"的注释，说明这个仓库对假开关有自觉：

- `settings.py:611` — "wiring-audit P2-5：删除孤儿配置 `obs_metrics_scrape_interval`（零读者）"
- `settings.py:284` — "wiring-audit P2-5：删除孤儿配置 `int_pipeline_total_timeout_ms`"
- `settings.py:35` — "wiring-audit P2-4：删除游离白名单副本…这份 8 项副本零引用"

但也有**承认仍在的**：`settings.py:299` 写 "`dispatch/fusion` 两段的预算字段（6.0s）自 T-Perf 起就是**孤儿配置**（全仓无读取点）"——即 `pipeline_dispatch_budget_s` / `pipeline_fusion_budget_s` 两个字段**仍然存在且仍无读者**（这两个是 float 不是 bool，未计入上面的 38 个）。

### 2.5 顺带发现：项目自台账里还有一串"未接线"

`docs-for-guiding generating docs/超声诊疗决策支持系统/05-可观测性与运维.md:470-480` 是一张**风险自评表**，我把它里与"部署运行态"相关的条目抄在这里（这些是项目自己的判断，不是我推断的）：

| 级别 | 条目 | 证据 |
|---|---|---|
| 🔴 | `internal/obs/*` 四端点**无认证**，其中 `cold-archive/run` 为**写操作** | `api/v1/obs_dashboard.py:18,48,85,93` |
| 🟠 | 追踪未接线（`init_tracing`/`instrument_fastapi` 无调用）+ `trace_id` 未注入日志 | `tracing.py:52,239,229` |
| 🟠 | 无 `request_id` 中间件，错误体的 `request_id` 多为空 | `main.py:516-530` |
| 🟠 | 无 Prometheus scrape 配置、无 alert rules 文件（仅 Alertmanager 路由） | 全仓检索 |
| 🟡 | `cache_monitor.start_prometheus_sync` 无调用方 | `cache_monitor/monitor.py:312` |
| 🟡 | `observability/dashboard.py` 的 router 未挂载（仅测试用） | `main.py:559-573` |
| 🟡 | `VictoriaMetricsMemoryMonitor` / `get_memory_monitor` 无调用方 | `metrics.py:287` |
| 🟢 | `observability/vm_backup.py` 未被使用（由容器承担） | `vm_backup.py:15` |

**怎么用这条**：如果答辩讲"我做了可观测性"，被追问到实现深度时，`obs_tracing_enabled` 默认 `False` 且 `init_tracing` 无调用方 —— 应该说"指标链路是通的（`/metrics` + VM 推送），分布式追踪只留了配置位、没接线"。**别说"有全链路追踪"。**

---

## 3. 部署形态

### 3.1 Dockerfile（4 个）

| 文件 | 基础镜像 | 用途 | 关键点 |
|---|---|---|---|
| `Dockerfile.dev` | `python:3.11-slim` | 主 API | `CMD uvicorn --host 0.0.0.0 --port 8000 --workers 1` |
| `Dockerfile.safety-gateway.dev` | `python:3.11-slim` | SG 独立容器 | 装 supervisor + gosu；非 root 用户 `sguser`；带 HEALTHCHECK |
| `Dockerfile.edge.dev` | `python:3.13-slim` | 边缘端 | **不装 TensorRT**（x86 装不上），TensorRT 懒加载退回 `NullNavBackend` |
| `Dockerfile.llama-cpp` | — | llama.cpp 引擎 | `MODEL_PATH=/models/...q4_k_m.gguf` |

⚠️ **注意 Python 版本不一致**：主服务/ SG 是 **3.11**，边缘端是 **3.13**。项目 `pyproject.toml` 未固定 `requires-python`（该段缺失），实机 venv 版本未在仓库中记录。

### 3.2 docker-compose（2 个文件）

| 文件 | 场景 | 服务数 |
|---|---|---|
| `docker-compose.dev.yml` | 开发/单机全栈 | **8 个服务**：postgres / redis / api / safety-gateway / edge / victoriametrics / alertmanager / vmbackup（末尾 `vmdata`/`ultrasound-network` 是卷与网络声明，不是服务） |
| `docker-compose.llm.yml` | LLM 引擎单独编排 | 2 个：vllm / llama-cpp |

⚠️ `docker-compose.llm.yml:41-43` 用 `networks: ultrasound-network: external: true`——必须先用 dev compose 创建网络，否则单独起会失败。

### 3.3 生产配置 vs 开发配置

**没有独立的 `docker-compose.prod.yml`。** 仓库只有 dev 后缀的编排。真机部署是**裸机 venv + 裸机进程**，不是容器：

- 主服务：`$HOME/uai/venv/bin/python -m uvicorn app.main:app`（`start_main.sh`）
- 各模型服务：`$HOME/uai/venv/bin/python scripts/start_embedding_services.py`（`server_start_embedding_services.sh`）
- vLLM：**独立 venv** `$HOME/uai/venv-vllm/bin/python`（`server_start_vllm_engine.sh:9`）
- 判官：`$HOME/uai/venv/bin/python scripts/llama_cpp_server.py`（`server_start_judge.sh`）
- 路径约定：代码在 `$HOME/uai/src`，模型在 `$HOME/uai/models`，日志在 `$HOME/uai/logs`，env 在 `$HOME/uai/.env`

**所以"容器化"只覆盖了中间件（PG/Redis/监控）与边缘端，不覆盖主服务与模型服务。**

### 3.4 硬件要求

真机规格（`.codebuddy/skills/remote-gpu-server-devtest/SKILL.md:31`）：

> GPU | RTX 5880 Ada 48GB（单卡 index 0）；nvcc 12.4

显存账（同文件 `:79`）：

> **GPU 显存账（48GB，串行启动）**：主生成 8002 0.46 + 判官 8006 0.36 + 嵌入×3 基线 6.8GB ≈ 45.4GB；
> 任何额外 GPU 进程须**临时停判官**再跑。双实例**并发启动会互踩 memory probe**，必须串行。

**这是最硬的一条约束**：48GB 卡上 45.4GB 已用，剩 ~2.6GB。加任何 GPU 进程都会 OOM。答辩若被问"能并发多少"，这组数字是唯一可引口径。

CPU/内存预算（设计口径，`06-开发环境与进度规划.md:50`）：64GB 标准配置，实测预算 12-22GB。但这与真机 48GB 显存卡的实际机器内存未在仓库中核对，标 **B**。

---

## 4. 依赖与版本

### 4.1 后端关键依赖（`requirements.txt`，84 行）

| 包 | 版本 | 备注 |
|---|---|---|
| fastapi | `0.110.0` | 生产在跑 |
| uvicorn | `0.29.0` | |
| pydantic | `2.13.5` | 从 2.6.4 上调以对齐生产实装（A.95 Q31） |
| sqlalchemy | `2.0.27` | |
| asyncpg | `0.29.0` | |
| redis | `5.0.1` | |
| qdrant-client | `>=1.12.0` | **下限约束，未锁死** |
| httpx | `0.28.1` | |
| prometheus_client | `0.20.0` | |
| opentelemetry-* | `1.24.0` | 四个包 + 三个 instrumentation |
| numpy | `>=1.24.0` | 下限约束 |
| minio | `>=7.2.0` | 下限约束 |
| rank-bm25 | `0.2.2` | |

### 4.2 版本锁定

| 文件 | 行数 | 作用 |
|---|---|---|
| `requirements.txt` | 84 | 直接依赖（多数 `==` 精确锁） |
| `requirements.lock` | **274** | 全量锁文件 |
| `requirements-edge.txt` | 15 | 边缘端额外（TensorRT 只能在 Jetson 装） |
| `requirements-emb.txt` | 18 | 嵌入服务额外 |

**有版本锁定，且带守卫**：`requirements.txt:12-14` 提到 `test_installed_versions_match_lock` 会拦"声明与实装漂移"。这是一条真实的漂移防线。

### 4.3 前端依赖（`frontend/package.json`）

| 包 | 版本 |
|---|---|
| react / react-dom | `^19.2.6` |
| antd | `^6.4.2` |
| `@cornerstonejs/core` / `dicom-image-loader` / `tools` | `^5.11.3` |
| echarts | `^6.0.0` |
| react-router-dom | `^7.15.1` |
| zustand | `^5.0.13` |
| vite（devDep） | 见 tailwind `^3.4.17`、typescript `~6.0.2` |

⚠️ 前端**全部是 `^` 范围**，没有 lock 化的精确版本（`package-lock.json` 存在与否未在本次核查范围内，但 `package.json` 本身不锁）。这和后端 `==` 风格相反。

### 4.4 前端部署方式

不是独立 Web 服务器，而是**由主服务挂载**：

```python
# app/main.py:928-933（注释）
# 产物在 app/static/ui/（由 `cd frontend && VITE_BASE=/ui/ npm run build` 生成，
# 为什么不直接 mount("/ui")：Starlette 按注册顺序匹配路由，mount("/ui") 一旦
# 先注册就会吃掉所有 /ui/*，SPA 回落路由永远匹配不到（实测 /ui/workspace 全 404）。
```

拆成两块（`app/main.py:940-946`）：`/ui/assets` 静态挂载 + `/ui/{path}` SPA 回落路由。另有 `/dashboard` 挂载（`main.py:924`）。

---

## 5. 可观测性

### 5.1 三层都有，但 tracing 默认关

| 能力 | 开关 | 默认 | 实现 |
|---|---|---|---|
| 指标采集 | `obs_metrics_enabled` | `True` | `app/observability/metrics.py` |
| `/metrics` 服务 | `obs_metrics_server_enabled`（`:620`） | `True` | `prometheus_client.start_http_server`，绑 `127.0.0.1:9090` |
| 推 VictoriaMetrics | `obs_vm_push_enabled` | `True` | `app/observability/metrics_collector/pusher.py` |
| **分布式 tracing** | `obs_tracing_enabled`（`:622`） | **`False`** | OTLP gRPC → `127.0.0.1:30100`（Langfuse，`:623`） |
| 缓存监控 | `obs_cache_monitor_enabled` | `True` | ⚠️ **但这是假开关**，见 §2.2 |

`settings.py:616-621` 有一段重要的自我修正记录（开关定义在 `:620-622`）：

> 此前这两条链路**只定义、从未接线**：`start_metrics_server()` 只被测试调用、`VictoriaMetricsPusher` 在 app 内从未实例化，导致运行期 Prometheus 抓不到 `/metrics`、VictoriaMetrics 收不到推送 —— 看板面板再正确也没有数据。

即 2026-09-28 才修好这条链路。这是我找到的最有说服力的"能力已实现但从未被调用"案例，且**已修复**。

### 5.2 告警

- Alertmanager 容器（`docker-compose.dev.yml:223`，端口 9093）
- 告警升级链：`alert_escalation_*` 配置组（`settings.py:643-649`）
- 内部 webhook 带 fail-closed 令牌：`alert_webhook_token` 为空不校验，**一旦配置即强制校验**（`settings.py:650-652`）

### 5.3 日志

- 统一 `structlog`，5 个存储后端：`file` / `postgresql` / `elasticsearch` / `batch` / `storage`（`app/observability/logging/`）
- trace 注入：`app/observability/tracing.py::inject_trace_to_structlog`
- 部署态日志文件：`$HOME/uai/logs/{api,sg,vllm,judge_qwen3,embed_bge_m3,embed_reranker,ultrasound_clip}.log`

---

## 6. 本机能不能跑起来

### 6.1 实测现状（这台 Mac）

| 检查项 | 实际 | 证据 |
|---|---|---|
| docker | **不可用**（`docker ps` 无输出） | 命令实测 |
| `.env` | **不存在** | `ls .env` → 无此文件 |
| 端口 8000-8006 / 9001 / 5432 / 6379 / 6333 / 18000 | **全部未监听** | `lsof -nP -iTCP -sTCP:LISTEN` 实测 |
| GPU | 无 NVIDIA 卡（macOS） | — |
| Python venv | 项目内无 venv | — |

**结论：本机当前是完全停摆状态，一个服务都没跑。**

### 6.2 跑起来缺什么（清单）

按阻塞强度排序：

| # | 缺什么 | 阻塞哪个服务 | 怎么解 |
|---|---|---|---|
| 1 | **`.env`**（`POSTGRES_PASSWORD` / `REDIS_PASSWORD` 必填） | compose 直接拒绝启动 | `docker-compose.dev.yml:7,40` 用 `${VAR:?}` 语法，未设即报错退出 |
| 2 | **PostgreSQL**（pgvector 镜像） | 主服务、SG 全挂 | 起容器或装本地 PG + `CREATE EXTENSION vector` |
| 3 | **Redis** | 队列、缓存、脱敏 HMAC 共享态 | 起容器 |
| 4 | **GPU + 模型权重**（约 45.4GB 显存） | 8002/8003/8004/8005/8006 | 本机无 NVIDIA 卡 → 只能连远程 GPU 服务器 |
| 5 | **模型文件**：`Qwen2.5-1.5B-Instruct`、`bge-m3`、`bge-reranker-base`、`biomedclip-base-patch16-224`、`Qwen3-8B-Q4_K_M.gguf` | 各服务启动即校验目录存在 | 从 HF / 内网镜像下载 |
| 6 | **Qdrant 二进制** | 向量检索（有 PGVector 降级） | `vector_index_auto_fallback=True` 可退 |
| 7 | **知识库已灌数**（3269 点） | 检索有结果但空 | 跑 `scripts/init_knowledge_base.py` + `server_populate_vector_index.py` |
| 8 | **HSM 私钥** `keys/jwt_private.pem` | 签发链路 | 仓库内已有该文件（但这是安全问题，见下） |
| 9 | 前端构建产物 `app/static/ui/` | `/ui/` 404 | `cd frontend && VITE_BASE=/ui/ npm run build` |

### 6.3 一条只跑后端单测的最小路径

不需要 GPU、不需要 PG/Redis 的全量单测是可行的（`make test` 用 mock）。但 `pyproject.toml` 的 `addopts` 强依赖 `-n 8 --dist=loadfile`（`AGENTS.md` 明确要求），本机需先有 pytest-xdist。

---

## 7. 本线最值得写进答辩的 3 个事实

按"评委追问时能不能站住"排序：

### 7.1 显存账 45.4 / 48 GB —— 这是"能不能加功能"的物理上限

```
主生成 8002 0.46 + 判官 8006 0.36 + 嵌入×3 基线 6.8 ≈ 45.4GB（48GB 卡）
```
来源：`.codebuddy/skills/remote-gpu-server-devtest/SKILL.md:79`

这句话能同时回答两个追问：「为什么不多跑几个模型」和「为什么必须串行启动」。单位是"份"不是 GB 也没关系——**关键是"已用满、剩 2.6GB"**这个事实。

### 7.2 服务数：本项目的 7 个 + 中间件 —— 说清楚"辅助工具"不是"一个模型"

主服务 / SafetyGateway / vLLM / 图像嵌入 / 文本嵌入 / 精排 / 判官 = **7 个自建服务**，加 PG/Redis/Qdrant/监控是 15 个条目。

这个数字能支撑「可信度是系统属性不是模型属性」的主张：**判官被刻意隔离成独立进程**（`server_start_judge.sh:3` 原话"与诊断双引擎隔离"），所以它不能反过来影响诊断。

### 7.3 3 个假开关 —— 主动交代"我以为能控的其实没接上"

`rag_use_bm25_fallback` / `rag_use_redis_cache` / `obs_cache_monitor_enabled` 全库零消费。

**这条怎么用**：这不是丑闻，是**同一类负结果**（和第 1 条"配置写了不等于生效"、第 3 条"融合层缺输入"完全同构）。如果答辩讲"三个负结果"，这三个假开关是"系统层面"那个负结果的**第二个独立证据**——而且比融合层那个更容易当场讲清（"我写了一个开关，全库没有一行代码读它"）。

---

## 8. 查不到 / 不能引用的

| 事项 | 状态 | 说明 |
|---|---|---|
| 真机当前是否在跑 | **查不到** | 本机无 ssh 动作；本文只核仓库配置，不核远程状态 |
| 真机实际内存总量 | **查不到** | SKILL 只记 GPU 48GB，未记系统 RAM |
| `pyproject.toml` 的 `requires-python` | **缺失** | 该字段在文件中不存在，Python 版本只有 Dockerfile 间接给出（3.11/3.13 不一致） |
| 生产 `.env` 的实际内容 | **不可引用** | 按 AGENTS 硬规则 #10，凭证不入库；本机也无 `.env` |
| `package-lock.json` 是否入库 | **未核** | 本次未展开前端锁文件 |
| 8006 判官的显存精确占用 | **仅 B 级** | SKILL 写 "0.36"（单位未注），非本项目实测报告 |
| "15 个服务"这个数 | **需注明口径** | 含中间件与监控；若评委问"你自己写了几个"，答 7 个 |

---

## 附：核查命令备查

```bash
# 服务与端口清单
grep -nE "container_name|ports:|image:" docker-compose.dev.yml
grep -nE "port|host" app/config/settings.py

# 假开关验证（全库零消费）
grep -rn "rag_use_bm25_fallback\|rag_use_redis_cache\|obs_cache_monitor_enabled" \
  --include="*.py" . | grep -v ".git"

# 主服务真实端口
grep -n 'PORT=' scripts/start_main.sh
grep -n "18000" scripts/server_restart_all.sh

# 判官不在配置体系
grep -c "8006" app/config/settings.py    # → 0

# 本机运行态
docker ps; lsof -nP -iTCP -sTCP:LISTEN | grep -E ":(8000|9001|5432|6379|6333)"
```
