# 踩坑档案（remote-gpu-server-devtest 的详细参考）

> 本文件是历史踩坑档案，供需要背景细节时查阅。**日常操作以 SKILL.md 为准**；
> 部分条目中的状态已过时（如 llama.cpp/8001 已于 2026-10-01 弃用、
> vllm_server.py 已被官方 OpenAI server 替换），以 SKILL.md 服务拓扑为准。
# 踩坑档案（remote-gpu-server-devtest 的详细参考）

> 本文件是历史踩坑档案，供需要背景细节时查阅。**日常操作以 SKILL.md 为准**；
> 部分条目中的状态已过时（如 llama.cpp/8001 已于 2026-10-01 弃用、
> vllm_server.py 已被官方 OpenAI server 替换），以 SKILL.md 服务拓扑为准。

---

# 远程 GPU 服务器（学校机）开发与真机测试

## 0. 一句话

驱动器服务器只用三件套：**密钥 SSH** + **scp 落盘脚本** + **显式 `bash` 执行**。
**不要**把复杂命令内联进 `ssh "..."` —— 远端默认 shell 是 **fish**，会解析并炸掉 `$?`、`$/` 之类。

## 1. 环境事实（2026-09-28 实测，改动请更新此表）

| 项 | 实测值 | 对开发的影响 |
|---|---|---|
| 主机 | `xsuper-7960-Tower` / `10.20.28.21` | 主机名本机 DNS 解析不到，**只能用 IP** |
| OS / 内核 | Ubuntu 24.04.5 LTS / `7.0.0-31-generic` | 与本地 Windows 不一致，脚本必须 LF + bash |
| 账号 | `student03` (uid 1004)，家目录 `/mnt/ee16/home/student03` | **共享机**，当时有 4 个用户在线 |
| **默认 shell** | **fish** | ssh 内联命令必须 fish 可解析，否则用 `bash file.sh` |
| CPU / 内存 | Xeon w7-3465X（56 逻辑核）/ **502 GiB**（空闲 490 GiB） | 足够跑 full 档 + 多进程评测 |
| **GPU** | **RTX 5880 Ada Generation，48 GB**，驱动 595.91.07（驱动报 CUDA 13.2） | **可跑 vLLM full 档**；`nvcc` 工具链为 12.4 |
| 磁盘 | `/` 818G 可用；`/mnt/ee16` 14T 可用；`/mnt/dd4` 3.3T 可用 | 模型权重放 `/mnt/ee16/home/student03/` |
| 配额 | `/dev/sdb` 上限 **4096G**，已用 < 1G | 空间不是约束 |
| Python | **3.12.3**，系统**无 pip**、缺 `ensurepip`、且 PEP 668 管控 | 必须自举 venv（见 §3） |
| 权限 | 普通用户，**按无 sudo 规划** | 不能 `apt install`，一切走用户级 |
| Docker | 29.8.1 + compose v5.5.1，**每人一个 dockerd**（socket `unix:///run/docker-student03.sock`，`DOCKER_HOST` 已设），开启 **userns-remap** | **可用于基础设施**（verified：`docker.1panel.live` 可拉 Redis/PG 等 `library/` 镜像，`docker run` 正常）；**CUDA 基础镜像无源** → GPU 推理走 venv |
| 端口 | 8000/8001/8002/9001/9090/5432/6379 **全部空闲** | 无冲突，但只许绑 `127.0.0.1` |
| 外网 | `hf-mirror.com` ✅ / `pypi.org` ✅ / `zenodo.org` ✅ / **`huggingface.co` ❌** / **`registry-1.docker.io` ❌** | 模型走 `HF_ENDPOINT=https://hf-mirror.com`；**镜像拉不动** |
| systemd | `--user` 可用但 **`Linger=no`** | 登出即停 → 常驻服务用 `nohup`/`tmux` 或容器 |

## 2. 接入（一次性）

```powershell
# 装公钥（唯一一次需要输密码；密码不要写进脚本/聊天）
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh student03@10.20.28.21 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

# 验证免密（应打印主机名）
ssh -o BatchMode=yes student03@10.20.28.21 hostname
```

已配置：本机 `~/.ssh/config` 里另有 `mlsys`（10.176.37.31），与本机无关勿混用。

## 3. 已知坑与正确姿势（**这是本 skill 的核心价值**）

| 坑 | 症状 | 正确姿势 |
|---|---|---|
| **fish 解析 ssh 命令** | `fish: $? is not the exit status`、`fish: $/ is not a valid variable` | 用 `ssh host 'bash -lc "..."'`（外层单引号给 PowerShell/fish，内层双引号给 bash）；或干脆 scp 脚本后 `bash file.sh` |
| **PowerShell here-string 管道加 BOM** | `bash: 行 1: set: 未找到命令`（`set` 前多了 `\ufeff`） | **不要把脚本用管道喂给 ssh**；先 `write_to_file` 落盘 → `scp` → `bash file.sh` |
| **CRLF 行尾** | `bash: $'\r': command not found` | 落盘后用 `sed -i 's/\r$//' file.sh`（或在本地以 LF 写） |
| **裸 `docker pull` 不通，但显式带源可拉** | `docker pull nvidia/cuda:...` → `Get "https://registry-1.docker.io/v2/": connection refused`（`daemon.json` 里配了 4 个 mirrors，**拉取时并未走它们**） | **显式带源主机名**：`docker pull docker.1panel.live/library/redis:7-alpine` |
| **镜像名前缀不要带 `https://`** | `invalid reference format` | 只取主机名：`docker.1panel.live/...`（我自己踩过） |
| **CUDA 基础镜像无源 → GPU 推理走 venv** | 实测 `docker.1panel.live/nvidia/cuda:...`=`manifest unknown`、`docker.m.daocloud.io/...`=`denied`、`docker.1ms.run/...`=`manifest unknown` | 不影响本项目：vLLM / llama.cpp 用**宿主机 venv 的 PyPI 包**直用 GPU；Docker 只承担 Redis / PostgreSQL（都是 `library/` 镜像） |
| **userns-remap 是否影响容器运行** | — | **不影响**：`docker run --rm docker.1panel.live/library/hello-world` rc=0。`--gpus` 仍无法验证（无 CUDA 镜像）；将来若要用容器跑 GPU，先解决镜像源，再试 `--userns=host` |
| **无 pip + 缺 ensurepip + PEP 668** | `python3 -m venv` 失败：`ensurepip is not available`；`get-pip.py --user` 被 PEP 668 拒绝 | `python3 -m venv --without-pip <venv>` 然后 `curl -fsSL https://bootstrap.pypa.io/get-pip.py \| <venv>/bin/python -`（**已验证可行，得到 pip 26.2.1**） |

## 4. 标准工作流

```powershell
# ① 侦察（只读，输出贴回分析）——脚本已在仓库里
scp scripts\recon_server.sh student03@10.20.28.21:~/
ssh -o BatchMode=yes student03@10.20.28.21 "sed -i 's/\r$//' ~/recon_server.sh && bash ~/recon_server.sh 2>&1 | head -200"

# ② 初始化（幂等：目录 + pip 自举 + venv + 后台装 torch）
scp scripts\server_bootstrap.sh student03@10.20.28.21:~/
ssh -o BatchMode=yes student03@10.20.28.21 "bash ~/server_bootstrap.sh 2>&1 | tail -40"

# ③ 查 CUDA 是否真的可用（注意：python 代码里不要出现引号，避免嵌套引号地狱）
ssh -o BatchMode=yes student03@10.20.28.21 'bash -lc "/mnt/ee16/home/student03/uai/venv/bin/python -c \"import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.device_count())\""'

# ④ 模型权重（必须走镜像；huggingface.co 不通）
#    export HF_ENDPOINT=https://hf-mirror.com
```

远端目录约定：`/mnt/ee16/home/student03/uai/{venv,models,data,reports,logs}`。

## 5. 端口与访问

- 服务一律 `127.0.0.1:<非默认端口>`；**绝不**把 PG(5432)/Redis(6379)/9090 暴露到校园网（共享机）。
- 从本机访问用端口转发，而不是开公网监听：
  `ssh -N -L 18000:127.0.0.1:8000 student03@10.20.28.21` → 浏览器开 `http://127.0.0.1:18000`。
- 3389 是共享机的 RDP，不要改动其配置。

## 6. 安全与合规红线

1. **不碰他人**：只操作 `student03` 自己的家目录/配额与**自己的 docker daemon**；不改全局 sshd、不改系统配置；需要 sudo 的动作先报给用户。
2. **不放患者数据**：这是共享服务器。甲状腺方向**无任何可商用公开数据集**（TN3K/TG3K 为 CC BY-NC；TNSCUI 为 CC BY-**ND 禁止演绎**）→ 真机测试只用公开可商用数据或自造数据。
3. **凭证**：不要在任何脚本/命令/聊天里出现密码；只用公钥（`BatchMode=yes`）。

## 7. 当前进度与待办（2026-09-28）

- ✅ 密钥连通、只读侦察完成、环境事实入表
- ✅ 无 sudo 初始化完成：`uai/` 目录 + venv + pip 26.2.1（**Python 3.12.3**，项目原定 3.13，注意兼容）
- ✅ **Docker 分工已实测确定**：`docker.1panel.live` 拉 `library/` 镜像 ✓（redis 7-alpine / postgres 16-alpine 已就位），`docker run` 正常 ✓；CUDA 基础镜像三源皆无 → GPU 走 venv
- ✅ **GPU 真机环境可用**：`torch 2.14.0+cu130` + `numpy 2.5.3`，`env CUDA_VISIBLE_DEVICES=0` 下 `cuda.is_available()==True`（默认 fish 配置里的 `2` 会让它恒为 False，见 §8）
- ✅ **基础设施已就位**（`uai/docker-compose.server.yml`，经 `docker.1panel.live` 拉镜像）：
  `uai-redis`（`127.0.0.1:16379`，requirepass，healthy，PING=PONG）与
  `uai-postgres`（`127.0.0.1:15432`，healthy，`pg_isready` 通过）；
  口令在服务器本地随机生成写入 `uai/.env`（600），**不回显**；数据用**命名卷**（避开 userns-remap 属主问题）
- ✅ **源码 + 依赖 + `.env` + 服务启动（2026-09-28 完成）**：`git archive HEAD` → `~/uai/src`；`pip install -r requirements.txt -r requirements-emb.txt` + 补装 10 个**清单里一直缺失**的启动期依赖（已回写进 `requirements.txt`）；`.env` 配 `DATABASE_URL`/`REDIS_URL`/HSM/`FUSION_LAYER_PORT=18000`/`OBS_VM_PUSH_ENABLED=false`；**主服务 `/api/v1/health` = 200 healthy**、**SG `/health` = 200**（独立进程 9001）
- ✅ **首轮真机缺陷已修**（见 §9，10 类，全部带守卫测试）：Redis 名字遮蔽、错误处理器 dict detail、脱敏引擎 import 期事件循环、RAG 装配参数、DSN 方言后缀、asyncpg interval 类型、SG 单进程校验、SG↔融合层探测路径与 degraded 语义、阻塞读超时、日志口令脱敏
- ⏳ **未完成**：`alembic upgrade head` 迁移链修复（0001 与模型定义不一致：缺列/缺表，现场用 `scripts/server_schema_from_model.py` 的 `create_all` 兜底建了 16 张表 + `alembic stamp head`）→ 经 `HF_ENDPOINT=https://hf-mirror.com` 下权重（`~/uai/models` 仍为空）→ 链路冒烟（**不产生数值**）→ 之后才是需权重的口径测试（双引擎一致性 ≥100 例/器官、分级一致率 ≥95%）
- ⚠️ 提醒：项目目标 Python 版本是 3.13，而服务器只有 3.12.3；若依赖对版本敏感，需装用户级 3.13（`uv`/`pyenv`，无需 sudo）

## 8. 🔴 头号坑：`CUDA_VISIBLE_DEVICES` 被写成了不存在的卡号

**症状**：`nvidia-smi` 看得到 GPU（38 MiB / 0%），但 `torch.cuda.is_available()` 恒为 `False`、`device_count()==0`。

**根因**：`/mnt/ee16/home/student03/.config/fish/config.fish:8` 有 `set -gx CUDA_VISIBLE_DEVICES 2`，而 `nvidia-smi --list-gpus` 只有 1 张卡（index 0）→ CUDA 被指向不存在的设备，等同于"屏蔽全部 GPU"。

**实测对照**（torch 2.14.0+cu130）：

| CUDA_VISIBLE_DEVICES | cuda.is_available() |
|---|---|
| `0` | **True（1 设备）** |
| 未设置 | **True（1 设备）** |
| `1` / `2` | False（0 设备） |

**处置（不修改用户配置，按会话覆盖）**：所有 CUDA 相关命令前加 `env CUDA_VISIBLE_DEVICES=0`，或在脚本里 `export CUDA_VISIBLE_DEVICES=0`。
**若将来学校给该账号加到多卡**，再用 `nvidia-smi -L` 的真实 index 重新确认，不要沿用这个 `2`。

**另一个小缺口**：venv 里还没有 `numpy`（torch 会告警 `Failed to initialize NumPy`）→ 装依赖时首装 `numpy>=1.24`。

**不要在外层双引号里用 `$变量`**（补一条引号坑）：`ssh host 'bash -lc "... $P ..."'` 中 fish 会**先展开** `$P`（未定义→空串），导致 `bash: -c: 未找到命令`。要么用字面路径（不引变量），要么 scp 脚本执行。

## 9. 应用侧真机缺陷速查（2026-09-28 首轮部署，全部已修并加守卫）

这些缺陷**单测永远抓不到**（全程 mock），只有真机启动才会显形。按症状查表：

| 真机症状 | 根因 | 位置 |
|---|---|---|
| `await` 报 `object bool/list/NoneType can't be used in 'await'`、`async for ... got generator` | `import redis.asyncio as redis` 后又 `import redis.backoff` → **名字被重新绑定为同步包**，`redis.Redis.from_url` 造出同步客户端；整个 Redis 层（缓存/队列/订阅/HSM 持久化）在异步代码里全废，还会**阻塞事件循环 5s**（同步 `xreadgroup(block=5000)`）→ 连带所有连接超时 | `app/config/redis.py` |
| 端口 LISTEN 但任何非 2xx 请求都像被黑洞吞掉（`curl http=000`） | `http_exception_handler` 把 dict 形态的 `exc.detail` 塞进 `error_message: str` → **处理器自己抛 pydantic ValidationError** → 响应发不出去 | `app/api/v1/errors.py` |
| 会话启动期 `RuntimeError: Cannot run the event loop while another loop is running` + `coroutine ... was never awaited` | 同步工厂里 `new_event_loop().run_until_complete()` 硬跑异步载入 → HMAC 密钥永不从 Redis 恢复，每次重启换密钥、历史令牌全部失配 | `app/security/desensitization.py` |
| `rag_service_init_failed` 且日志**没有原因** | ① `BM25Index(session_factory=..., vocabulary=...)` 里的 `vocabulary` 参数不存在；② `Retriever` 缺 `session_factory`（缺了只返回 placeholder 正文）；③ 这些告警走 stdlib `logging` 的 `extra={}`，而项目渲染器是 structlog → **payload 被整个丢掉** | `app/main.py` |
| `Qdrant→pgvector fallback` 后仍 `[E2003_VEC_CONNECTION_FAILED]` | `asyncpg.create_pool` 不能吃 SQLAlchemy 的 `postgresql+asyncpg://`（`+asyncpg` 后缀只有 SQLAlchemy 认） | `app/config/dsn.py` + `pgvector_client.py`、`infra_health.py` |
| `pg_health_check_error ... 'str' object has no attribute 'days'` | asyncpg 的 `interval` 参数要 `datetime.timedelta`，原实现传字符串 `"300.0 seconds"` | `app/observability/infra_health.py` |
| `SafetyGateway ... Application startup failed` + `actual_workers=0` | 单进程校验写的是 `children != 1`，而它的文档写着"单 worker 时正常就是 0 个子进程" → **SG 在任何干净环境下都起不来** | `safety_gateway/main.py` |
| SG 恒 `degraded` + 主服务每 4s 一条 `critical` | ① SG 探融合层用 `/health`，主服务真实路由是 `/api/v1/health` → 恒 404；② 主服务把 SG 的 `degraded`（设计内可服务状态）当 unhealthy；③ 阈值引擎懒加载未预热，`/health` 因此恒报 degraded | `safety_gateway/health/fusion_probe.py`、`app/services/safety_gateway_client.py`、`safety_gateway/main.py`、`threshold.py` |
| `sg_redis_stream_consume_error error=Timeout reading from ...` 每 ~6s | 阻塞读 `block=5000ms`，而 `socket_timeout=redis_pool_timeout=5s` → 同秒打平必超时 | `safety_gateway/redis_pool.py`、`app/config/redis.py` |
| 日志里出现明文口令 | `logger.info("sg_redis_pool_initialized", url=<带口令URL>)`、`infra_health` 的 `redis_url=` | 统一用 `app/config/dsn.py::redact_url` |
| `soft_hsm_expired days_overdue=52` | 仓库根 `soft_hsm_enabled_at.txt` 是**被提交的运行期状态**；90 天有效期一到，签名路径抛 `RuntimeError` | 现场用 `scripts/server_fix_soft_hsm.sh` 备份+刷新 |

**一条贯穿性教训**：上述 10 条里 8 条属于"能力已实现但从未在真实环境跑过"。判据很简单 —— **凡是从未在真机启动过的东西，视同未实现**。

## 10. 启动 / 重启 / 健康检查标准姿势

```bash
# 基础设施（容器，127.0.0.1:16379 redis + 15432 postgres）
bash ~/server_infra_up.sh

# SG（独立进程，9001）+ 主服务（18000）一起重启并探活（会顺带补 .env）
scp scripts/server_restart_all.sh student03@10.20.28.21:~/
ssh student03@10.20.28.21 "sed -i 's/\r$//' ~/server_restart_all.sh && bash ~/server_restart_all.sh"

# 两侧健康逐项摘要（紧凑，不刷屏）
bash ~/server_check_health.sh

# 只看最近一次 RESTART 之后的启动段 + 错误计数
bash ~/server_log_segment.sh
```

**部署期必须写进 `~/uai/.env` 的三项**（不属于项目默认值，属本机部署事实）：

```
FUSION_LAYER_PORT=18000      # SG 的 fusion probe 默认探 8000
OBS_VM_PUSH_ENABLED=false    # 本机没有 VictoriaMetrics（默认 URL 是 compose 服务名）
SOFT_HSM_TIMESTAMP_PATH=/mnt/ee16/home/student03/uai/src/soft_hsm_enabled_at.txt
```

**健康路由**：主服务 `/api/v1/health`（**不是** `/health`）；SG `/health`。
**已知可接受的 degraded**：SG 的 `oom_protection=warning`（需特权容器 `--oom-score-adj=-1000`，本机无 sudo 无法设置）→ SG 整体显示 `degraded` 属正常，主服务只记一条 warning。
**日志含 NUL**：`api.log` 会被 grep 判成二进制，统一 `tr -d '\000' < api.log > api.clean.log` 后再读。

## 11. 权重下载（hf-mirror 三个必踩的坑，2026-09-28 实测）

`scripts/server_download_models.sh` + `server_verify_models.py`（只做结构校验，不出数值）：

1. **必须 `HF_HUB_DISABLE_XET=1`**：huggingface_hub ≥0.32 默认走 Xet 存储后端，它要访问
   `cas-server.xethub.hf.co` —— **hf-mirror 不镜像该域名**，报
   `CAS Client Error: HTTP status client error (401 Unauthorized)` 并中断下载。关掉即回退常规 HTTP。
2. **`--exclude` 每个 pattern 都要单独写一次**：多值形式只吃第一个，其余会被当"显式文件名"
   （警告 `Ignoring --exclude since filenames have been explicitly set.`），于是
   `BAAI/bge-m3` 里的 `imgs/.DS_Store` 照样被请求 → `403 Forbidden` → **整次下载中断，
   config.json 都没落地**（表现为"下了 2.2G 但目录里没有权重文件"）。
3. bge-m3 的 `onnx/`（约 2GB）本项目用不到，一并 `--exclude`。

已下好并校验：`~/uai/models/{qwen2.5-1.5b-instruct-gguf,bge-m3,bge-reranker-base}`
（GGUF 魔数+元数据可读、HF config/tokenizer 可解析）。
**Ultrasound-CLIP（768d 图像嵌入）是项目自有产物**（由 `finetune_ultrasound_clip.py` 训练，
公开源没有）—— 图像嵌入服务暂时无法独立起。

## 12. 真机端到端冒烟（`scripts/server_link_smoke.sh` + `server_smoke_probe.py`）

**它一次就抓出两个阻断级缺陷**（都是"提交 200 但请求永远停在 processing、结果接口 404"）：

1. **request_id 双 ID 分叉**：端点先 `_generate_request_id()` 用于"入队+响应+查询"，
   而 `DiagnosisRepository.create_request()` 内部**又生成一个**并落库 → worker 拿队列里的 id
   查库永远查不到（`pipeline_request_not_found`）。判据：端点里 `created_id` 从未被使用。
2. **入队早于提交**：`create_request` 只 `flush()`，worker 用**另一个会话**读库，
   未提交的行看不见；worker 空转（BLPOP 立刻返回）时必然踩中。

**关键操作事实**：
- `POST /api/v1/diagnosis/request` **需要鉴权**（无 token → 401 E1003），`users` 表迁移后是空的
  → 冒烟脚本运行时生成口令写 `~/uai/.smoke_pw`(600) 并建 `smoke-doctor`（明文不进 argv/仓库）；
- 前置数据（patients/sessions/consent_records）由脚本直接 INSERT（幂等），不去猜会话/同意 API 的 payload；
- `python /path/to/script.py` 会把**脚本所在目录**而非 CWD 放进 `sys.path` → 从 `$HOME` 跑
  那些 `import app.*` 的脚本必须在文件里显式 `sys.path.insert(0, "~/uai/src")`；
- 当前终态（无 LLM/嵌入服务时）：`status=failed`、`ai_diagnosis_failed=True`、
  `requires_manual_review=True`、`review=安全校验服务不可用，请转为人工诊断` —— 即**控制面全通、
  按设计 fail-closed**，这不是故障而是缺推理后端的预期表现。

## 12.5 嵌入/重排薄服务（2026-09-28 新增，补齐仓库缺失的服务端）

仓库里 `app/main.py:199-212` 把 8003/8004/8005 当 HTTP 客户端连，但**全仓库没有服务端实现**
（scripts/、compose、`deploy/supervisor/` 都没有）→ 现在由
`scripts/start_embedding_services.py` 补上（transformers 直载官方权重，只绑回环）：

```bash
# 启动（在 ~/uai/src 下；脚本自动 export CUDA_VISIBLE_DEVICES=0）
bash ~/server_start_embedding_services.sh     # 8004 bge-m3 / 8005 bge-reranker
bash ~/server_check_embedding.sh              # 只打印形状/状态码/健康布尔，不出数值
```

**契约（照客户端源码，改服务端前先读它）**：
| 端点 | 请求 | 响应 |
|---|---|---|
| `POST /v1/embeddings` (bge-m3) | `{"texts":[...],"return_dense":true,"return_sparse":bool}` | `dense_embeddings`（**必须 1024 维、条数==texts 数**）|
| `POST /v1/rerank` | `{"query":str,"documents":[...],"top_k":int,"model":str}` | `results:[{index,relevance_score}]`，`index` 是 documents **原始下标** |
| `GET /health` | — | 200 即视为可用（客户端只看状态码）|
| `GET /v1/model/info` | — | `model_name/dimension/max_input_length/version` |

实测结果：`rag_health_partial_failure bm25=False embedding=True reranker=True vector_index=True`
（修好前 embedding/reranker 全为 False，日志里是 `E2002_EMB_CONNECTION_FAILED`）。

**`bm25` 为什么仍是 False**：`BM25Index` 懒构建、**生产代码此前从不调 `ensure_built`**
（已在 `app/main.py` 启动期补上预热，真机日志 `bm25_index_built num_chunks=0`）；
知识库为空时 `ready=False` 是正确语义——要 `bm25=True` 得先真正建知识库
（`scripts/init_knowledge_base.py`，现在嵌入服务已就绪，这是它第一次可用）。

## 12.6 vLLM 引擎（8002，独立 venv）

```bash
bash ~/server_install_vllm_isolated.sh   # 装到 ~/uai/venv-vllm（8G）
bash ~/server_start_vllm_engine.sh       # 起 8002（只绑回环）
```

**为什么要独立 venv**：vLLM 会拉自己的 torch（实测 `vllm 0.30.0` + `torch 2.13.0+cu130`，venv 8.1G），
装进主 venv 会覆盖/降级主 venv 的 torch —— 而 8004/8005 两个嵌入服务正在用主 venv 的 torch。

**必踩的坑**：vLLM 0.30 的采样默认走 **flashinfer，首次调用要 JIT 编译**，它调的是
**PATH 上的 `ninja` 可执行文件**；本机无 sudo 装不了 apt 的 ninja-build，只有 venv 里的
python 包 → 真机报 `FileNotFoundError: [Errno 2] No such file or directory: 'ninja'`
（栈：`v1/worker/gpu/sample/sampler.py` → `flashinfer/jit/cpp_ext.py run_ninja`），
且整条链最终只抛一句 `RuntimeError: Engine core initialization failed. See root cause above.`。
处置（脚本里已做）：
1. `export PATH="$UAI/venv-vllm/bin:$PATH"`（pip 的 ninja 包自带可执行文件）；
2. `export VLLM_USE_FLASHINFER_SAMPLER=0` 关掉 flashinfer 采样，退到原生 torch 采样 ——
   少一个 JIT 环节（本机 nvcc 12.4 而 torch 是 cu130，JIT 编译链本就错配）。
处理后 `/health` 200（约 28s 就绪）、chat completion 返回非空文本。

## 12.7 让诊断真正跑通到 `completed`（2026-09-29，第 1 例真实诊断的最后一公里）

按顺序踩掉的坑（**每一条都是"单测全绿、真机必炸"**）：

| 症状 | 根因 | 位置 |
|---|---|---|
| `requires_manual_review` 恒真，`review=检测到禁忌条件：检测到4条禁忌` | 应用**从不发送** `diagnosis_data`；而 SG 的禁忌判定在拿不到条件数据时**按设计判命中**（`tests/unit/sg/test_sg_contraindication.py` 钉住了这条保守语义） | 管线原本只发 organ/findings/recommendation |
| 同上一份诊断在写处 pass、读处判禁忌 | **两个** `check_output` 调用点各自拼 payload：读路径（`GET /api/v1/diagnosis/result/{id}`）直接把 worker 落库的整包 findings 当 `output_data` | `app/api/v1/diagnosis.py` + `diagnosis_pipeline.py` |
| `dense_search_failed [E2003_VEC_COLLECTION_NOT_FOUND] table vector_index_knowledge_chunks not found` → `self_rag_retries_exhausted` | 向量表由**建库索引路径**创建，而知识库是空的（0 chunk） | 需先建库/建集合 |

**处置（已固化）**：
1. `patient_context` 新增结构化 `conditions`（`None` = 未提供 → 不转发，保持 SG 保守；`[]` = 已确认无条件 → 转发）；
2. 落库补列（迁移 **0014**）——此前 `create_request(patient_context=...)` **收了就丢**；
3. 发往 SG 的 `output_data` 收敛到**唯一构造点** `app/services/sg_output_data.build_sg_output_data()`，
   写路径与读路径共用（守卫：凡调用 `check_output` 的函数都必须调用它）；
4. 冒烟前置：`bash ~/server_run_bootstrap_kb.sh`（建 `vector_index_knowledge_chunks` + 灌 3 条
   **自造、已标注 `smoke_fixture=true`** 的条目 + 同步写 `knowledge_chunks`，让稀疏腿也有内容）。

**结果**：`status=completed`、`ai_diagnosis_failed=False`、`requires_manual_review=False`、
`desensitization_status=full`，段内 error/critical=0。
残留 warning（不阻塞）：`dense_search_failed [E2003_VEC_SEARCH_FAILED]`（表已存在、客户端自己回查也成功，
但应用侧带 filter 的检索仍失败 —— 待查 filter/类型细节）。

**冒烟请求必须显式给 `patient_context.conditions`**（现在是 `{"age":45,"gender":"female","conditions":[]}`）；
省略该键 ≠ 空列表，前者会让 SG 走保守分支。

## 13. 现场/未完成项（别把它当成"已做完"）

| 项 | 状态 |
|---|---|
| LLM 推理后端 | **未起**：需要 `llama-cpp-python`（PyPI 默认 wheel 是 CPU-only，CUDA 需第三方 wheel 源）或 vLLM（`full` 档强制、pip 重且与 torch/cu130 有冲突风险） |
| 嵌入/重排服务 | 权重已就位，**HTTP 服务未起**（`ultrasound_clip_port`/`bge_m3_port`/`bge_reranker_port`） |
| Ultrasound-CLIP 权重 | **公开源没有**（项目自有产物），图像嵌入腿无法端到端验证 |
| SG 的 `oom_protection` | `warning`（需 `--oom-score-adj=-1000`，无 sudo 设不了）→ SG 整体恒 `degraded`，主服务只记一条 warning（已按设计区分） |
| 需权重的口径测试 | 未开始（双引擎一致性 ≥100 例/器官、分级一致率 ≥95%） |

**验收口径提醒**：`tests/unit` 全绿（5779）与覆盖率 82.22% ≥ 82% 只证明"离线可测的那一层"；
真正的"能出诊断结论"取决于上表前两项。

## 14. 图像嵌入腿（8003）与模型底座（2026-09-29）

**官方 Ultrasound-CLIP 权重当前不可达（结论固定）**：HF 仓库 `JJY-0823/Ultrasound-CLIP`
是**空壳**（仅 .gitattributes，所以作者把权重放 OneDrive）；OneDrive 分享链接
`1drv.ms/f/c/48250be4328ce7ab/IgCnIA0-...` 经 301 → `onedrive.live.com`（带
`migratedtospo=true`）——**本机与学校服务器均无法连接 onedrive.live.com（被墙）**；
api.onedrive.com / SPO v2.0 autoredeem 全部 401；download.aspx 返回 SharePoint 错误页；
本机无可用代理端口；workers.dev 等海外中转全 000。要拿官方权重只能：有代理时浏览器手动
下载，或等作者把权重传到 HF。⚠️ 该仓库与 US-365K 均**未声明 license**（注册商用前必须确认）。

**现行底座：BiomedCLIP（MIT 许可，微软官方）**，已接入真机 8003：
- 权重 `microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224`（open_clip 格式
  784MB）经 hf-mirror 下载到 `~/uai/models/biomedclip-base-patch16-224`；
  ⚠️ 仓库名不是 `microsoft/biomedclip-base-patch16-224`（hf-mirror 会 401）；
- 其 HF 格式第三方转换版（`chuhac/BiomedCLIP-vit-bert-hf`）在 transformers 5.17 下
  加载即崩（`CLIPConfig accepts only keyword arguments` 位置参数问题）——**别用它**；
- **I2T@10 = 23.6%**（US-365K test 500 样本，随机基线 2%；通用基座
  clip-vit-large-patch14 = 9.6%）——BiomedCLIP 是基座的 2.5 倍，采纳为现行底座；
- 维度 512 ≠ 768：`~/uai/.env` 加 `EMBEDDING_DIM=512`（客户端声明维度只来自
  settings.embedding_dim，向量表以实际写入向量建表）。

**open_clip 3.3 加载要点**（`scripts/eval_i2t_openclip.py` + 服务端 OpenClipBackend）：
- 用 **`local-dir:` schema**：`create_model_and_transforms(f"local-dir:{dir}")` +
  `get_tokenizer(f"local-dir:{dir}")` —— 从目录里 open_clip_config.json 读配置，
  tokenizer 同源（BiomedBERT）。临时文件 `add_model_config` 方案有两个坑：
  配置顶层是 `{"model_cfg":...}` 需剥壳，且注册名是 stem，`get_tokenizer` 用
  basename（带 .json）会查不到 → **静默回退 SimpleTokenizer**（BiomedBERT 词表
  完全对不上，文本嵌入全废）；
- `CustomTextCLIP`（hf_text 类模型）**没有顶层 `embed_dim`**，维度在
  `model.text.output_dim`（HFTextEncoder）；
- `HFTokenizer.__call__` 直接返回**纯张量**（input_ids，padding='max_length'），
  没有 attention_mask 键——token_count 只能近似 numel；
- 服务启动必须带 `HF_ENDPOINT=https://hf-mirror.com`，否则 get_tokenizer 下载
  BiomedBERT tokenizer 时卡死在被墙的 huggingface.co（表现为"Waiting for
  application startup"挂 4 分钟+）。

**transformers 5.x 大坑**：服务器 venv 是 **transformers 5.17.0**，`get_text_features`/
`get_image_features` 从"返回张量"改为**返回 `BaseModelOutputWithPooling`**（投影特征在
`pooler_output`）——`clip_model.py` 与服务端都已做版本兼容（`_features_tensor`）。

**8003 服务端**：`scripts/start_embedding_services.py --service ultrasound-clip`
（`--clip-format hf|open_clip` 选加载器；端点契约照 `app/embedding/ultrasound_clip.py`
客户端：JSON 文本 / multipart `image` / multipart `images`+texts JSON 串）。
真机启动：`bash ~/server_start_ultrasound_clip.sh [model_dir]`（自动识别格式，
幂等，探活）。功能冒烟：`scripts/server_check_ultrasound_clip.py`。

## 15. 真机第二轮三连坑（2026-09-29，全部已修 + 守卫测试）

| 症状 | 根因 | 修复 |
|---|---|---|
| 重启后第 1 例诊断 `completed`，第 2 例起恒 `needs_review`（review=安全校验服务不可用），SG `POST /api/v1/safety/check` 全 503（E4005 sg_p99_throttling） | **SG p99 节流自持死锁**：启动期样本只有 2 个（冷启动 8.11ms）→ p99≈max 超 5ms critical 阈值 → 3 次轮询后节流 → 新请求被拒 → 永远没有新样本 → p99 冻结在超限值 | `latency_tracker.py`：样本数 < `sg_p99_min_samples_for_throttle`（默认 20）时 p99_status=unknown、清零计数，绝不节流。测试 `tests/unit/sg/test_latency_tracker_throttle_min_samples.py`（原 `test_sg_p99_throttling.py` 的 helper 同步改为整窗同值样本） |
| `rerank_failed_using_heuristic`（bge-reranker 8005 返回 500）且此后所有请求 500（进程粘性毒化） | **max_length 超过模型位置嵌入上限**：bge-reranker-base 是 XLM-R 架构（max_position_embeddings=512），服务默认 `--max-length 1024` → 序列 >512 时 transformers 5 在 `torch.gather(buffered_token_type_ids, index=position_ids)` 处 scatter-gather 越界 → **CUDA device-side assert**（粘性，只能重启进程）。实测触发阈值在 125~500 字符之间 | `start_embedding_services.py`：三个后端（reranker/bge-m3/clip）加载后统一把 max_length 钳制到 `max_position_embeddings - 2`（守卫测试在契约测试文件里）。调试姿势：`CUDA_LAUNCH_BLOCKING=1` 重启后请求一次，日志直接给出越界算子 |
| `dense_search_failed [E2003_VEC_SEARCH_FAILED] pgvector search failed`（表存在、客户端直查成功、应用侧带 filter 检索必失败） | `pgvector_client.py` 生成 SQL 漏单引号：`metadata->>organ = $1::text` —— PG 把 `organ` 当**列名**解析 → `column "organ" does not exist`。`detail` 里有真实错误但 retriever 日志只打了 `str(exc)`，长期查不到 | 补上引号：`metadata->>'{safe_key}'`（safe_key 已过白名单正则，无注入）。**守卫教训**：原测试把带 bug 的 SQL 形状 `metadata->>organ` 钉进了断言 —— 断言"能跑通的 SQL"之前先验证它真的能跑。复现姿势：用生产客户端 + `.env` 的 DATABASE_URL 直调 `PgvectorVectorIndex.search`，打印 `exc.detail` |

**调试通用教训**：这三条的日志都只给 generic message，真实错误藏在 `exc.detail` /
CUDA 栈 / SQL 文本里。第一步永远是"用生产代码路径最小复现 + 打印完整异常"，
而不是反复重启碰运气。

## 16. 真机端到端冒烟（本轮后终态，2026-09-29）

`bash ~/server_link_smoke.sh`：`status=completed`、`ai_diagnosis_failed=False`、
`requires_manual_review=False`、`desensitization_status=full`，**error=0**。
剩余 warning 均为设计内：`safety_gateway_health_probe_degraded`（oom 保护无 sudo 设不了）、
`qdrant_unhealthy_falling_back_to_pgvector`（真机无 Qdrant，pgvector 是设计降级路径）。

## 17. 双引擎一致性评测（≥700 例，三轮已完成）

- 用例：`scripts/server_build_eval_cases.py` → `app/inference/data/consistency_test_cases.json`
  （780 例 = 常规 240 + 边界 440 + 高风险 100；甲状腺/肝脏/心脏）
- 跑批：`python scripts/consistency_verify.py --cases ... --vllm-model 'Qwen2.5-7B-Instruct'
  --structured-output --use-chat --concurrency 4 --timeout 120 --output ~/uai/reports/...`
  （PYTHONPATH=~/uai/src 必须带，见 §12 的 sys.path 坑）
- **三轮结果（2026-09-29）**：基线 1.5B 23.33%（提取失败 61）→ A2 1.5B+结构化+chat
  **80.51%**（提取失败 6）→ A3 **7B** +同设置 80.38%（提取失败 0）。
  **基座规模无净收益**——剩余 ~19.6% 是边界模糊用例的设计内分歧，≥95% 门控口径
  需项目组决策（边界/常规分开统计或语义等价口径），详见 16 号文档 A.2.1。
- 报告：`tests/reports/consistency_780_report.json`（基线）、
  `..._a2_report.json`、`..._a3_7b_report.json`
- **引擎基座现状**：8001/8002 已升级为 Qwen2.5-**7B**-Instruct（GGUF Q8_0 + fp16，
  vLLM gpu-memory-utilization 0.4）。8002 的 model 名为 `Qwen2.5-7B-Instruct`。
  llama.cpp `--n_ctx 8192`（4096 会在 KB 知识注入后溢出，见 §18）。
- 数据：US-365K（71,919 test 图文对 + images 36.4 万张，`~/uai/data/us365k`）与
  AUL（肝脏，CC BY 4.0，`~/uai/data/aul`）已就位；台账见 `docs/合规/数据源台账/`

## 18. 真实知识库（kb_v1.0.0）与三颗"从未接线"钉子（2026-09-29）

**KB 现状**：409 文档（甲状腺癌诊疗规范 2018 九章节 9 份 + 中文医疗对话甲状腺
QA 400 份，Apache 2.0，源码 `Toyhom/Chinese-medical-dialogue-data`）→ 433 chunks
（bge-m3 1024 维）。Gate 1 检索准确率 hybrid Recall@10 = **98% ≥ 85% ✅**
（报告 `tests/reports/t1_kb_retrieval_eval.json`）。语料组装：
`scripts/server_assemble_kb_v1.sh`（PDF 章节 + `scripts/t1b_build_dialogue_docs.py`）。

**三颗钉子（全部"实现了但从未跑通"类，建库时实测拔出）**：
1. `knowledge_chunks.embedding` ORM 是 `Vector(768)`，bge-m3 是 1024 → 已改 ORM
   + 真机 `ALTER TABLE ... TYPE vector(1024)`（正式 alembic 迁移文件待补）；
2. `hybrid_indexer.py` 的 `on_conflict_do_update(set_=…)` 字符串键是**数据库列名**，
   写 ORM 属性名 "meta" 而物理列是 `metadata` → 首次建库即崩（已修 +
   `build_upsert_statement()` 编译级守卫 `tests/unit/knowledge/test_hybrid_indexer_upsert.py`）；
3. **接线缺口**：`init_knowledge_base.py` 只写 `knowledge_chunks`，生产检索器读
   `vector_index_knowledge_chunks` → 建库后向量索引表为空。灌数：
   `PYTHONPATH=~/uai/src python scripts/server_populate_vector_index.py
   --kb-version kb_v1.0.0 --organ thyroid`（幂等，经生产客户端）。

**vLLM V1 孤儿进程大坑**：`VLLM::EngineCore` 是**独立子进程**，`pkill -f
vllm_server.py` 杀不死它——两个孤儿曾占 33GB 显存导致 llama.cpp 8k ctx 分配失败
段错误。重启 vLLM 必须两条都执行：`pkill -f vllm_server.py; pkill -9 -f
'VLLM::EngineCore'`。显存核对：vLLM 0.40（19GB）+ llama.cpp 8k（8.4GB）+
三个嵌入服务（~6GB）≈ 34GB / 48GB。

**上下文窗口上线级教训**：KB 知识注入后诊断 prompt 达 5943 tokens > 4096 →
`generation_failed`（且检索腿真正生效的副作用恰恰是 prompt 变长）。
llama_cpp_server 必须 `--n_ctx 8192` 起。

## 21. P1：llama-server 迁移与 Langfuse（2026-09-30，数据见 16 号 A.8）

**① 服务载体迁移**：8001 已从 llama-cpp-python 包装换成**官方 llama-server
二进制**（`~/uai/models/llama-b11272/llama-server`，ubuntu-cuda 预编译，
编排 `scripts/server_start_llama_server.sh`）。关键参数：
- `--ctx-size 32768`：**ctx 是总池，`--parallel 4` 会切分**——8192 总池下
  每 slot 仅 2048，诊断 prompt 5943 tok 直接 400（真机实测）
- `--threads 8 --flash-attn on`：单请求 56s→20.7s（2.7x，prefill 是大头）
- 客户端契约不变（OpenAI 风格 + grammar 字段透传）；健康探活同 /health

**② llama.cpp grammar 采样器特性（最终判定）**：官方 server 上 guided
仍**恒输出枚举首分支**（780 例 thyroid 一致率 0.0，a='2' b='3' ×173）——
greedy+grammar 掩码下分布坍缩是 llama.cpp 采样器特性，非实现 bug。
**等价性评测只用 prompt 级口径（A2，87.31%）**；约束解码只用于生产
单引擎确定性输出。

**③ Langfuse v3 自托管**（~/uai/langfuse，全栈 6 容器，web 127.0.0.1:30100）：
- `CLICKHOUSE_MIGRATION_URL=clickhouse://clickhouse:9000`——**http:// 和
  chclickhouse:// 都是无效 driver**（报 unknown driver http/chclickhouse）
- 首启 org/project 固定 keys：`LANGFUSE_INIT_ORG_ID`（不是 ORGANIZATION_ID）
  + `LANGFUSE_INIT_PROJECT_PUBLIC_KEY/SECRET_KEY`；验证：
  `curl -u pk:sk /api/public/projects` 返回 project 即就绪
- OTLP 接入：主服务 tracing.py 用**显式构造的 OTLPSpanExporter**（gRPC）——
  显式构造不读 OTEL_EXPORTER_OTLP_HEADERS env，Basic auth 必须经
  `headers=` 参数传（settings.obs_tracing_otlp_headers）；endpoint 是
  gRPC channel（无 path）：`http://127.0.0.1:30100`
- compose 文件改后必须**scp 到 ~/uai/langfuse/docker-compose.yml** 再 up
  （部署目录与仓库目录不同步是重启不生效的常见原因）

**④ 服务器侧依赖**：另一窗口的 PyJWT 替换只改了 requirements——**服务器
venv 要手动 `pip install PyJWT`**，否则主服务启动即崩（ModuleNotFoundError: jwt）。

**⑤ 管线时延**：端到端仍 >150s（fusion/Self-RAG 串行 LLM 链），专项待做；
`RESULT_TIMEOUT_SECONDS` 已放宽 30→150（diagnosis.py），冒烟轮询窗口 150s。

**KB 级 golden set**：从建好的 chunks 直接构造（语料=chunks，查询=chunk 首句
截断），评测器同 `t1_eval_retrieval.py`——见 `scripts/_t1b_kb_eval.sh` 已删，
重建姿势在 16 号 A.5。

## 19. T1c 调优与忠实度（2026-09-29，数据见 16 号 A.6）

- **QA 型 golden**：136 条真实患者问题，gold=来源文档（source_doc 级相关性，
  chunk 变体可复用）。`scripts/t1b_build_qa_golden.py`（服务器跑，corpus 从
  PG 取）。QA 集基线 R@10=99.3%（比自指 golden 更真实但同样高）。
- **调优结论**：等权 RRF(k=60) 已最优；rerank 只加召回不加排序质量；
  chunk 尺寸不敏感（420~459 chunks 三版本等价）。**检索侧调优可收工**。
- **faithfulness 基线 56.3% vs 门控 95% ❌**：主因 7B 判官 NLI 过严 +
  生成腿上下文外扩写。脚本 `scripts/t1_eval_faithfulness.py`（RAGAS 官方
  prompt 在 `scripts/prompts/ragas_faithfulness.py`）。判官 JSON 必须
  `json_repair` 兜底（纯 json.loads 首轮 84% 崩）。
- **chunk_id 跨版本撞车钉子**：`_build_chunk_id` 现含 kb_version 前缀
  （`kb_v1.0.0:doc#0000`）。**旧版多版本共存会静默覆盖其他版本行（PK 级）**。
  重建后必须 `server_populate_vector_index.py --purge` 清向量索引旧行。
- **pkill 自杀坑**：`ssh ... "pkill -f <模式>; nohup ..."` 当命令行自身含
  模式串时 pkill 会杀掉自己的 shell——后台任务要么分开两次 ssh，要么前台跑。

## 20. 管线超时四钉子（2026-09-30，T2c 实测，数据见 16 号 A.9）

1. **DB 会话跨 LLM 调用 = 必死**：管线 session 包住分钟级 LLM 链，
   `idle_in_transaction_session_timeout` 掐断空闲事务连接，写库
   `underlying connection is closed`。**pool_pre_ping 救不了事务中途
   断连**。姿势：session 只包读写段，LLM 段无 DB 上下文，写终态用
   独立短会话（参照 `_mark_result_cancelled` 模式）。
2. **llama.cpp `--cache-reuse` × `--parallel N` 死锁**：分片 KV
   （kv_unified=false）下 prefix 缓存跨 slot 查找引发排队风暴——串行
   TTFT 4.8x 提速的假象，并发后 90s 超时×重试=181s。llama.cpp 要
   prefix caching 必须 parallel=1+unified KV；多并发走 vLLM。
3. **诊断轮询路由**：`GET /api/v1/diagnosis/result/{request_id}`
   （不是 /requests/，probe/脚本写错会全数 404 假超时）。
4. fusion Layer2（一致性）/Layer3（Self-RAG）输入同输出独立，
   应 gather 并行——串行是 fusion 19.6s 的主要成分。
5. **生成引擎已切 vLLM（2026-09-30）**：`~/uai/.env` 设
   `DEPLOYMENT_PROFILE=full` + `INFERENCE_ENGINE=vllm` +
   `VLLM_DEFAULT_MODEL=Qwen2.5-7B-Instruct`。vLLM 2677-token 请求
   1.9s（llama.cpp CPU 同任务 40s，**20x**）；长文本单例端到端 35s。
   llama-server（8001）已停用（省 8.4GB 显存），回滚=删 .env 两行 +
   `bash ~/server_start_llama_engine.sh`。**llama.cpp 是 compact 档
   CPU 病态慢的根源，勿再回切**。
6. 服务器卫生：/tmp 探针脚本用完即删；评测日志（t2c_*/t2a_*/consistency_*）
   归档后删；GPU 进程合法清单=嵌入×3+vLLM（8002 必须监听，否则
   EngineCore 是孤儿要杀）。
7. **评测器终态口径**：管线终态全集 = completed / failed / needs_review
   （评测脚本轮询漏 needs_review 会把 2 秒完成的请求"等"成 240s
   poll_timeout——T2c 的"批次互相影响/124s"假象根源，2026-10-01 实测
   9/9 全部 1.5~7s 到终态）。写评测器先 curl /diagnosis/result 看真实
   status 取值。
8. **worker 池**：int_worker_pool_size 默认 4，并发评测先调 16
   （.env）——管线是 IO 密集协程，4 worker 必然排队；真正的并发
   调度在 vLLM 引擎侧（continuous batching）。
9. **llama.cpp 速度之谜破案**：llama-b11272 预编译包是 **CPU-only
   构建**（ldd 无 CUDA 库，--n-gpu-layers 被静默忽略）——设计定位
   是 GPU 引擎，部署包选错了构建，7B 全靠 8 线程 CPU 跑（40s/
   2677tok）。若要回用 llama.cpp 必须自行编译 CUDA 版或选
   cuda-linux-x64 官方包。**2026-10-01 已彻底弃用 llama.cpp。**
10. **vLLM 必须用官方 OpenAI server**：`python -m
    vllm.entrypoints.openai.api_server`（原生 async + continuous
    batching + prefix caching）。自写 `LLM()`+uvicorn 包装**没有
    batching**——并发完全串行（GPU 100% 但吞吐 61 tok/s）。官方
    server + 0.80 显存 + 16384 batched tokens：780 例 3.25h→5min。
    三个启动必需环境：`export CUDA_VISIBLE_DEVICES=0`（fish 默认
    指向不存在的卡）、`PATH=venv-vllm/bin:$PATH`（ninja）、
    `VLLM_USE_FLASHINFER_SAMPLER=0`（flashinfer JIT 需 CUDA 12.8，
    服务器 12.4）。
11. **结构化输出用 `response_format: {"type": "json_schema", ...}`**——
    旧 `extra_body.guided_json` 在新版 vLLM 已静默失效（不报错但
    不约束）。评测脚本走 `openai.AsyncOpenAI` 官方 SDK，不要手写
    urllib。
12. **PowerShell 多行 python -c 会静默失败/转义爆炸**——本机向服务
    器传代码一律用 PowerShell here-string 管道：`@'...'@ | ssh host
    "cat > /tmp/x.py && python /tmp/x.py"`。
13. **并发压测标准姿势**（2026-10-01 实测）：httpx.AsyncClient 全异步
    （登录/建会话/提交/轮询全程复用连接池）；session 由 API 生成
    （POST /api/v1/sessions 返回服务端 id）；前置数据（患者/同意）
    用 asyncpg 直插。结论：16 worker + 官方 server，**8~32 并发下单
    例 pipeline 时延恒定 9.5~9.7s（batching 生效的铁证）**，吞吐瓶颈
    =管线内 LLM 串行链（dispatcher 4.7s + fusion 4.7s），属链长问题
    而非并发机制问题。压测脚本 `/tmp/_load8.py`（服务器）。

## 22. T2 系列评测（2026-09-30，数据见 16 号 A.7）

- **数据渠道三连堵**：TNSCUI Zenodo 只有 PDF；CACTUS FRDR http=000；
  ALN-ULTRA Zenodo files=0；CARDIACUDC 需 Kaggle 凭证。**AUL（~/uai/data/aul，
  Benign/Malignant/Normal 目录天然分类）与 US-365K caption 词标注是唯二
  可用的带标注真实数据**。
- **SG p99 节流已停用**（sg_config `sg_p99_min_samples_for_throttle=10^9`）：
  串行 p99 8.7ms、并发 41ms，任何静态阈值都自持死锁——负载波动下基于延迟
  的节流不可用，须改错误率熔断。恢复节流前 T2 类批量评测必挂。
- **管线吞吐硬上限 ≈1 例/26s**：fusion 19.6s（Self-RAG/CRAG 串行 LLM 链）
  × llama.cpp 单并发（无 --parallel）。**并发提交只会积压超时**，评测必须
  串行 + 轮询窗口 ≥240s。llama.cpp 无 --parallel/--flash-attn 参数。
- **端到端响应无结论文本**：completed 响应的 patient_explanation/
  malignancy_risk/measurement 全 null，全 JSON 无诊断文本（KB 检索腿正常）。
  任何"评 AI 结论"的评测在此修复前都不可执行。
- 主服务 API 端口是 **18000**（不是 8000）；评测用户 smoke-doctor 密码在
  `~/uai/.smoke_pw`；session 必须先 INSERT（E1002 强校验，模板抄
  server_link_smoke.sh）。

## 23. 2026-10-01 ~ 10-02 增量踩坑（数据见 16 号 A.10~A.16）

> 本节只收「操作层」姿势；数字与结论归 16 号附录 A。

**① 会话级工具损坏（2026-10-02）**：某会话中 `write_to_file` / 长参数
`replace_in_file` 全挂——**换会话自愈**；新会话开工先写 2 行小文件验工具，
别直接上生产编辑。（与服务器无关，但会直接毁掉一次部署轮次。）

**② CACTUS / FRDR 直下（2026-10-02）**
- FRDR 备份维护结束已恢复（09-30 还 http=000，10-02 frdr-dfdr.ca 301→200；
  **旧域名 frdr.usask.ca 的 DNS 已死**）。
- 匿名直下 = `/repo/files/<path>` 跟随 302 到 `data.globus.org`；
  目录**不服务 index**，清单走 `/repo/userfiles` API（五参：
  item_id/collection_id/endpoint_id/path/limit/offset）。
- **文件清单以 `frdr-dfdr-checksums.txt` 为权威**——README 会虚标
  （CACTUS 称 37,736 张，实收 29,963，缺 PSAV 全部 5,832 与 PL 1,941）。
- 下载器入库 `scripts/cactus_download.py`（ThreadPoolExecutor 32 +
  重试退避 + 断点续传）；12 个视频跳过（M1/M6 不需要）。
- 渠道实测（服务器侧）：frdr-dfdr.ca ✅ / opendatalab.org.cn ✅ / kaggle.com ✅
  / gitee.com ✅ / storage.googleapis.com ⚠️400 / drive.google.com ❌ /
  frdr.usask.ca ❌。

**③ open_clip 本地权重加载（T2a/T2b 同款）**：`create_model_and_transforms("local-dir:" + 路径)`；
**路径直传 model name 会报 not found in built-ins**；`open_clip_train` CLI 需
repo 版库，脚本层（训练脚本）用官方 API 即可。

**④ T2b 训练一键窗口**：`scripts/t2b_cactus_run.sh`——停双 vLLM → 训练 →
**串行恢复**（rc 透传），需服务窗口批准；首启踩了 ③ 的坑。bs 256 显存 ~21GB。

**⑤ 8003 能力端点（懒加载）/ 契约**：`POST /v1/classify`（T2a）、
`POST /v1/analyze_view`（T2b）——微调 visual **独立副本**懒加载，首次调用才占显存，
不污染基线检索权重。批量嵌入契约是 `/v1/embeddings/multimodal`。

**⑥ 图谱查询 P2-KB-08（commit 6d693e5 / 0eeb3cb）**：`graph_query_enabled`
默认 **False**（一期设计内关闭）；真机 PG 跑 CTE 报
`AmbiguousParameterError`——asyncpg 无法推断 NULL/数组参数类型，
**须显式 `CAST(...)`**。边投影回填 `scripts/kb_backfill_relations.py`。

**⑦ UDT 词汇表四层管线（16 号 A.15③/A.16）**：L1 THUOCL_medical 现成词表
（jsdelivr CDN 取，本地 raw 截断不可用）+ 解剖锚点过滤；L2 语料对齐；
L3 LLM 抽取（8002 + `response_format json_schema`）；LS 生产同义词。
**通用 prompt + 关键词过滤对低频危象词召回极差（85→1）——专项 prompt +
指向性语料（指南文档）才是正解**（cardiac 危象 22 词即此法）。

**⑧ 词表资产化模式**：`app/inference/data/hallucination_vocab.json`（含
version/source），加载失败**回退内置默认 + logger.warning 留痕**——运营侧可维护。
Pydantic 默认 `extra=ignore` 让加字段天然兼容，但**显式声明字段**才是可追溯正道
（如 `ContraindicationRule.source_reference`）。

**⑨ 评测预检按端口分别校验**：gen/judge 分端口（8002/8006）后，
只查 `gen.list` 会误报 FATAL——fail-fast 逻辑本身要跟着部署演进。
