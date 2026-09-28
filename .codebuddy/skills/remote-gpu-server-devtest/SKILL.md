---
name: remote-gpu-server-devtest
description: 连接并驱动「学校 GPU 服务器」（student03@10.20.28.21 / xsuper-7960-Tower）做超声诊疗决策支持系统的开发与真机测试。当需要在该服务器上执行命令、部署、拉模型、跑真机测试，或遇到「ssh 远程命令报 $? 错」「bash: set: 未找到命令」「docker pull 连接被拒」「venv 创建失败 ensurepip」等问题时使用。
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
