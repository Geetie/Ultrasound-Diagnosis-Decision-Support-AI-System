---
name: server-deploy-and-repair
overview: 系统性地完成学校 GPU 服务器（student03@10.20.28.21）的部署并把部署过程中暴露的项目真实缺陷逐项修复（仓库侧提交 + 守卫测试），跑通 API 服务与链路冒烟，再进入需模型权重的口径测试。
todos:
  - id: unblock-api-lifespan
    content: 用 [skill:remote-gpu-server-devtest] 定位 lifespan 卡点并起 SafetyGateway 与 Qdrant，使 /api/v1/health 返回 200
    status: completed
  - id: repo-runtime-fixes
    content: 固化 redis ping 兼容、infra_health 默认值、requirements 补 11 依赖，并加启动期导入覆盖守卫
    status: completed
    dependencies:
      - unblock-api-lifespan
  - id: alembic-chain-repair
    content: 用 [subagent:code-explorer] 产出模型列差异清单，0001 逐表对齐并补 safety_events，真机重跑迁移
    status: completed
    dependencies:
      - repo-runtime-fixes
  - id: hf-mirror-weights
    content: 经 hf-mirror 下载 embedding 与 LLM 权重并验证可加载，确定档位与推理引擎
    status: completed
    dependencies:
      - alembic-chain-repair
  - id: link-smoke-no-numbers
    content: 真机链路冒烟（健康检查+最小诊断），显式声明不产出任何数值结论
    status: completed
    dependencies:
      - hf-mirror-weights
  - id: final-verify-gate
    content: 全量回归与覆盖率门禁复核（整体≥82/核心≥86），汇总提交、遗留项与现场项
    status: completed
    dependencies:
      - link-smoke-no-numbers
---

## 项目背景

本项目（超声诊疗决策支持 AI 系统）已从"本机开发"推进到"真机部署"：学校 GPU 服务器 `xsuper-7960-Tower`（10.20.28.21）已完成接入与基础设施搭建，但**应用尚未服务就绪**，且部署过程暴露了一批**从未在真实环境跑过**的项目缺陷。

## 核心需求

系统性地完成服务器部署，并把部署过程中暴露的缺陷逐项修复回仓库（含守卫），最终跑通链路；过程中不停顿、不反复征询。

## 具体内容

**一、让服务真正起来**

- 定位并解除应用启动（lifespan）阻塞，使健康检查接口返回 200，HTTP 服务可用。
- 补齐启动所依赖的外部进程与依赖项（独立安全网关进程、向量库或按配置关闭向量检索）。
- 解决启动期反复出现的连接超时与重连报错，使启动日志无致命错误。

**二、把部署暴露的缺陷固化回仓库**

- 依赖清单不完整：全新环境启动即崩（缺十余个启动期依赖），需补齐并增加"清单必须覆盖启动期导入"的守卫。
- Redis 连接自检一处写法在特定客户端版本上必然失败，导致缓存/队列/订阅在应用内全部不可用，需兼容修复。
- 可观测性模块中 Redis 地址硬编码，不读取统一配置，导致健康检查永远探测错误地址。
- 数据库连接参数放置位置不被驱动接受，导致应用完全连不上数据库，需修正并验证。
- 数据库迁移脚本与模型定义不一致（表名、缺列、缺整张表），导致迁移在任何环境都无法执行，需按"改动面最小且符合原设计语义"对齐并实测。

**三、模型与验证**

- 通过可达的镜像源下载所需模型权重，确认可加载，并确定运行档位与推理引擎。
- 完成链路冒烟，明确声明不产出任何数值结论；之后再进入需权重的口径测试。

**四、收尾**

- 全量回归与覆盖率门禁复核，汇总未完成项与现场项，一次性提交推送。

## 边界与红线

- 服务器为共享机：只操作本人账号的家目录、配额与专属容器守护进程；不改系统级配置、不碰他人资源。
- 真实患者数据不得上共享机；相关方向缺少可商用公开数据集，测试只用公开可商用或自造数据。
- 服务端口仅绑定本机回环地址并使用非默认端口，访问经由隧道转发，绝不对外暴露。
- 口令等凭据不得出现在脚本、命令或对话中。
- 链路冒烟阶段不得输出任何数值结论。

## 技术栈（沿用现状，不引入新架构）

- **运行环境**：Ubuntu 24.04.5 / Python 3.12.3；无 sudo，采用用户级 venv（`venv --without-pip` + 注入 pip，绕过 `ensurepip` 缺失与 PEP 668）。
- **容器**：学生专属 dockerd（`unix:///run/docker-student03.sock`，userns-remap 已开）；镜像**必须显式带源主机名**经 `docker.1panel.live` 拉取（裸 pull 会被 `registry-1.docker.io` 拒绝）。
- **基础设施**：Redis 7 / PostgreSQL 16 + pgvector（均为 `library/` 与 `pgvector/` 命名空间，已实测可拉），仅绑 `127.0.0.1:16379` / `127.0.0.1:15432`，数据用命名卷。
- **GPU 推理**：宿主机 venv + PyPI 包（Docker 侧无可用 CUDA 基础镜像）；`torch 2.14.0+cu130` 已装，RTX 5880 Ada 48GB。
- **模型来源**：`HF_ENDPOINT=https://hf-mirror.com`（`huggingface.co` 不可达）。
- **测试**：pytest + ruff（含棘轮守卫）；仓库既有约定为 `git commit --no-verify` + `git -c lfs.locksverify=false push`。

## 实施方案

### 阶段 A：解除启动阻塞（先让服务能服务）

1. 用 `tr -d '\000'` / `grep -a` 读取被识别为二进制的 `api.log`，定位 lifespan **最后一条成功日志**与阻塞点（当前已知：端口已 LISTEN 但 `/api/v1/health` 持续 `000`）。
2. **启动 SafetyGateway 独立进程**（`safety_gateway/`，独立 uvicorn，端口 9001，仅绑回环）——它是设计内独立进程，未启动则输出侧校验与健康检查必失败。
3. **处理 Qdrant**：优先按 pgvector 的成功经验尝试拉取 `qdrant/qdrant` 容器（若失败则通过配置关闭向量检索，保证启动不被外部依赖拖住）；两者都记录为可回退开关。
4. 复跑启动直至 `/api/v1/health` 返回 200，且日志中无致命错误。

### 阶段 B：部署期缺陷固化回仓库（每项含守卫）

| 缺陷 | 修法（改动面最小 + 原设计语义） |
| --- | --- |
| `app/config/redis.py:44-49` `await client.ping()` | 改为「可等待则 await，否则直接取布尔」；保留原 `except` 语义 |
| `app/observability/infra_health.py:20,176` 硬编码地址 | 默认值回落 `settings.redis_url`（本机已有 `.bak` 与服务器改动需对齐） |
| `requirements.txt` 缺 11 项 | 补齐 10 项；`pkuseg` 单列并注明 py3.12 无 wheel 的处置（`jieba` 已装） |
| `app/config/database.py:18-19` | 已改为 `server_settings`（+ `idle_in_transaction_session_timeout`），补守卫 |
| 迁移链（`alembic.ini`、`0001`、`0004`） | 已修日志三段/表名/`sa.JSONB`；再按模型逐表补齐 `0001` 缺列并补建 `safety_events` |


新增守卫（**防再犯**，比修单个 bug 更重要）：

- `tests/unit/quality/test_requirements_cover_startup_imports.py`：**静态扫描 `app/` 的全部第三方导入，断言其集合被 `requirements*.txt` 覆盖**——这是"清单不全导致全新环境必崩"的唯一根治手段。
- `tests/unit/data/`：迁移与模型一致性守卫（对 `alembic/versions/*.py` 的 `create_table` 与 `app/models/database.py` 的 `__tablename__`/列做集合比对，缺表/缺列即失败）。
- `tests/unit/config/`：Redis 自检兼容性守卫（ping 返回协程与返回布尔两种替身都要通过）。
- `tests/unit/obs/`：`infra_health` 默认地址必须来自 settings（源码断言 + 行为断言）。

关键接口约定（守卫契约）：

```python
# tests/unit/quality/test_requirements_cover_startup_imports.py
def startup_third_party_imports() -> set[str]:
    """AST 扫描 app/**.py 的顶层 import，剔除 stdlib 与本地包，返回第三方顶层模块名。"""

def declared_requirements() -> set[str]:
    """解析 requirements*.txt 的需求名（含模块名→包名映射，如 yaml→pyyaml）。"""

# 断言：startup_third_party_imports() - declared_requirements() == set()
```

### 阶段 C：模型与档位

- 经 `HF_ENDPOINT=https://hf-mirror.com` 下载 embedding（`ultrasound-clip` 与 `bge-m3-medical`，dim 768/1024 双列）与 LLM 权重到 `~/uai/models`；校验可加载（不产出指标）。
- 档位决策：`compact` 强制 `llama.cpp`、`full` 强制 `vLLM`（`settings.py:522-533`），且 `full` 下 `vllm_gpu_memory_utilization` 必须落在 0.70–0.95。**默认按 `llama.cpp`/compact 先跑通**，再评估是否切 full。

### 阶段 D：链路冒烟与收尾

- 冒烟：健康检查 + 一次最小诊断（`env CUDA_VISIBLE_DEVICES=0`），**显式声明不产出任何数值结论**。
- `final-verify`：全量回归 + 覆盖率门禁（整体 ≥82、核心 ≥86），并核对豁免清单棘轮未被稀释。

## 实现要点（执行细节，防止返工）

- **远端 shell 是 fish**：内联命令中凡含  `的元素必须放进单引号；可靠姿势是**本地落盘 `.sh` → `scp` → `ssh "sed -i 's/\r$//' ~/x.sh && bash ~/x.sh"`**；不得用 PowerShell here-string 管道喂 ssh（会加 BOM）。
- **GPU 命令一律前置 `env CUDA_VISIBLE_DEVICES=0`**（用户 fish 配置里的 `2` 指向不存在的卡，会静默屏蔽 GPU）。
- **口令不落日志/不回显**：`.env` 保持 600；容器内自检（`redis-cli -a "$REDIS_PASSWORD"`）在容器内展开变量。
- **`pip install a b c` 中任一失败会整批不装**——补依赖必须分组或逐条，避免误判为网络问题。
- **迁移修复不得盲改**：以 `app/models/database.py` 为权威（`DiagnosisSession.__tablename__ == 'sessions'`），先产出"模型列 vs 迁移列"差异清单，再改；改完必须在真机 `alembic upgrade head` 实测通过。
- **服务器改动需与仓库对齐**：`infra_health.py` 在服务器上有 `.bak`、`redis.py` 与 `database.py` 有服务器侧补丁，落地时要保证"仓库 == 服务器"同一份内容，避免两处漂移。
- **提交粒度**：每个阶段结束即提交推送（`--no-verify` + `lfs.locksverify=false`），保持可回退。

## 目录结构（改动清单）

```
project-root/
├── app/
│   ├── config/
│   │   ├── database.py                      # [MODIFY] connect_args→server_settings（已改，补守卫）
│   │   └── redis.py                         # [MODIFY] _is_connected 兼容 ping 的协程/布尔两种形态
│   └── observability/
│       └── infra_health.py                  # [MODIFY] :20 已改；:176 默认值回落 settings.redis_url
├── alembic/
│   ├── alembic.ini                          # [MODIFY] 已补 [loggers]/[handlers]/[formatters] 三段
│   └── versions/
│       ├── 0001_initial_migration.py        # [MODIFY] 表名已对齐；再补缺列与 safety_events 建表
│       └── 0004_audit_cross_chain.py        # [MODIFY] sa.JSONB → PG 方言 JSONB（已改）
├── requirements.txt                         # [MODIFY] 补 10 项启动期依赖；pkuseg 单列注明处置
├── tests/unit/
│   ├── config/test_redis_client_compat.py   # [NEW] ping 两种返回形态都要通过
│   ├── data/test_migration_model_alignment.py # [NEW] 迁移表/列必须覆盖模型（缺表缺列即失败）
│   ├── obs/test_infra_health_defaults.py    # [NEW] 健康检查地址必须来自 settings
│   └── quality/test_requirements_cover_startup_imports.py # [NEW] 启动期导入必须被依赖清单覆盖
└── scripts/
    ├── server_*.sh                          # [NEW] 已产出的运维脚本（侦察/初始化/依赖/DB/Redis/启动）需入库归档
    ├── server_start_safety_gateway.sh        # [NEW] 以回环地址启动独立安全网关进程并验证
    ├── server_download_models.sh             # [NEW] 经 hf-mirror 下载并校验权重
    └── server_link_smoke.sh                  # [NEW] 冒烟：健康检查 + 最小诊断（不产出数值）
```

## Agent Extensions

- **Skill: remote-gpu-server-devtest**
- Purpose: 承载本任务所有服务器侧操作的既有协议（fish 引号规则、先落盘再 scp、显式镜像源、只绑回环、凭据不回显、`CUDA_VISIBLE_DEVICES=0`），并持续回写新踩到的坑。
- Expected outcome: 每一轮服务器操作可复现、无引号/BOM 类失败；skill 的进度与待办章节与真机状态一致。

- **SubAgent: code-explorer**
- Purpose: 在阶段 A 定位 lifespan 中的阻塞调用点与启动期外部依赖编排；在阶段 B 产出"模型列 vs 迁移列"的完整差异清单，避免盲改迁移。
- Expected outcome: 给出带 `path:行号` 的阻塞点清单与逐表列差异，作为修复与守卫测试的直接输入。