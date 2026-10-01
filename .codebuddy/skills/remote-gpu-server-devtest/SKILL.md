---
name: remote-gpu-server-devtest
description: 连接并驱动「学校 GPU 服务器」（student03@10.20.28.21）做超声诊疗系统的部署与真机测试。当需要 ssh 到该服务器执行命令、启动/重启 vLLM 与主服务、跑评测/压测/建知识库、或遇到 fish 报错、CRLF、CUDA 不可见、vLLM 启动失败等问题时使用。详细踩坑档案见 reference.md。
---

# 远程 GPU 服务器（student03@10.20.28.21）操作手册

## 铁律（先读）

1. 远端默认 shell 是 **fish**：复杂命令一律本地写脚本 → `scp` → `ssh host "bash /tmp/x.sh"`。
2. 传代码用 PowerShell here-string 管道：`@'...'@ | ssh host "cat > /tmp/x.py"`（需 bash 执行时先落盘再 bash）。
3. 脚本上传后先 `sed -i 's/\r$//'` 去 CRLF。
4. 所有 CUDA 进程前 `export CUDA_VISIBLE_DEVICES=0`（fish 配置写了不存在的卡 2）。
5. 服务只绑 127.0.0.1；不碰他人目录；密码不进命令行。

## 环境事实

| 项 | 值 |
|---|---|
| GPU | RTX 5880 Ada 48GB（单卡 index 0）；nvcc 12.4 |
| OS / Python | Ubuntu 24.04 / 3.12.3（无 sudo，venv 自举） |
| 家目录 | /mnt/ee16/home/student03/uai/{src,venv,venv-vllm,models,data,reports,logs} |
| 外网 | hf-mirror.com 与 pypi 可达；huggingface.co 与 docker registry 不通 |

## 服务拓扑（2026-10-01）

| 端口 | 服务 | 启动/探活 |
|---|---|---|
| 18000 | 主 API（FastAPI） | /api/v1/health；重启见 reference.md R1 |
| 8002 | vLLM 官方 OpenAI server（Qwen2.5-7B-Instruct） | /health；命令见下 |
| 8004 / 8005 | bge-m3 / bge-reranker | bash ~/server_start_embedding_services.sh |
| 8003 | BiomedCLIP 图像嵌入 | bash ~/server_start_ultrasound_clip.sh |
| 9001 | SafetyGateway | 随 ~/server_restart_all.sh |
| 16379 / 15432 | Redis / PG 容器 | bash ~/server_infra_up.sh |
| 30100 | Langfuse（docker 全栈） | ~/uai/langfuse/docker-compose.yml |


## vLLM 重启（8002，三环境变量缺一即崩）

```bash
set -u
UAI="$HOME/uai"
export CUDA_VISIBLE_DEVICES=0
export PATH="$UAI/venv-vllm/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0
pkill -9 -f "VLLM::EngineCore" 2>/dev/null
pkill -9 -f "vllm_server.py" 2>/dev/null
cd "$UAI/src"
nohup "$UAI/venv-vllm/bin/python" -m vllm.entrypoints.openai.api_server \
  --model "$UAI/models/Qwen2.5-7B-Instruct" --served-model-name Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.80 \
  --max-num-batched-tokens 16384 > "$UAI/logs/vllm.log" 2>&1 &
```

- 必须用官方 OpenAI server：自写 LLM()+uvicorn 包装没有 batching，并发实为串行（GPU 100% 但吞吐 61 tok/s）。
- 三钉子：缺 CUDA_VISIBLE_DEVICES 报 pynvml InvalidArgument；缺 PATH 报 ninja not found；缺 VLLM_USE_FLASHINFER_SAMPLER=0 报 nvcc --compress-mode（需 CUDA 12.8）。
- 杀进程连 VLLM::EngineCore（独立子进程）一起杀。

## 主服务重启（18000）

```bash
PID=$(ss -ltnp | grep ':18000' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$PID" ] && { kill $PID; sleep 4; kill -9 $PID 2>/dev/null; }
cd "$UAI/src"; set -a; source "$UAI/.env"; set +a
nohup "$UAI/venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 18000 \
  > "$UAI/logs/api.log" 2>&1 &
```

重启后核对 `ps -o lstart` 晚于脚本文件 mtime（防旧进程假活）。pkill 与重启不能同一条 ssh 命令（命令行含模式串会自杀）。健康路由：主服务 /api/v1/health（不是 /health）。日志含 NUL 先 `tr -d '\000'` 再 grep。

## 评测标准命令

```bash
# 780 例分级质量（官方 server + response_format 结构化输出），约 5 分钟
cd ~/uai/src && PYTHONPATH=$HOME/uai/src nohup ~/uai/venv/bin/python \
  scripts/server_consistency_vllm_single.py \
  --cases app/inference/data/consistency_test_cases.json \
  --no-guided --concurrency 32 --output reports/p02.json \
  > ~/uai/logs/p02.log 2>&1 &

# faithfulness（RAGAS 口径；生成+判官都走 8002）
PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python scripts/t1_eval_faithfulness.py \
  --golden-set data/golden/kb_qa_golden_v1.json --sample 60 \
  --generator-url http://127.0.0.1:8002 --output reports/t1_faith.json

# 并发压测（httpx 全异步；结论见 reference.md P 节）
PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python /tmp/_load8.py
```

评测注意：
- 结构化输出用 `response_format: {"type": "json_schema", ...}`；旧 `extra_body.guided_json` 在新版 vLLM 静默失效。
- 官方 server 校验 model 名精确匹配 `Qwen2.5-7B-Instruct`（硬编码旧小写名会 404）。
- judge/生成均走 8002（8001 llama.cpp 已弃用）。

## KB 建库 / 灌数

```bash
PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python scripts/init_knowledge_base.py \
  --kb-version kb_v1.1.0 [--chunk-min-tokens N --chunk-max-tokens N]
PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python scripts/server_populate_vector_index.py \
  --kb-version kb_v1.1.0 --purge
```

- 对话语料参数化：scripts/t1b_build_dialogue_docs.py（--organ/--keyword/--sources）。
- 灌数幂等；重建后必须 --purge 清旧索引行。chunk_id 含 kb_version（多版本防撞车）。
- 现状 kb_v1.1.0：809 文档 / 856 chunks（thyroid+liver+cardiac 三器官）。

## 核心坑速查（完整叙事见 reference.md）

| 症状 | 根因与修复 |
|---|---|
| fish: $? is not the exit status | ssh 内联被 fish 解析，落盘脚本 bash 执行 |
| bash: $'\r': command not found | CRLF，sed -i 去除 |
| torch.cuda.is_available()=False | fish 的 CUDA_VISIBLE_DEVICES=2，export =0 |
| docker pull connection refused | 显式带源 docker.1panel.live/library/... |
| vLLM pynvml InvalidArgument | 缺 CUDA_VISIBLE_DEVICES=0 |
| vLLM FileNotFoundError: ninja | 缺 venv-vllm/bin 在 PATH |
| vLLM nvcc --compress-mode | export VLLM_USE_FLASHINFER_SAMPLER=0 |
| 诊断响应 404 / 永远 processing | DB 会话跨 LLM 调用断连，写终态用独立短会话（已修） |
| pkill 后服务"还在" | pkill 匹配自身 ssh 命令行自杀，重启没执行；核对 ps lstart |
| api.log 判为二进制 | 含 NUL，先 tr -d |
| 知识库建完 dense 查不到 | 建库只写 knowledge_chunks，需灌 vector_index（server_populate_vector_index） |
| guided_json 不生效 | 新版 vLLM 用 response_format json_schema |
| 评测 completed=9/9 但 support=None/结论空 | 评测器 _pick_conclusion_text 缩进错位 → UnboundLocalError 被主循环 except 吞——跑完必须抽样 ai_conclusion 非空 + 查 report 的 error 字段；单跑评测函数对真实响应 1 分钟定位 |
| needs_review=6/9 假象 | 旧进程 BM25 索引早于三器官灌数构建；重启自愈——评测前先重启主服务再灌数序 |
| 并发评测排队 | int_worker_pool_size 默认 4，.env 调 16（IO 密集协程） |

## 压测结论（2026-10-01）

- 8/16/32 并发端到端诊断：单例 pipeline 时延恒定 9.5~9.7s（官方 server batching 生效铁证）。
- 吞吐瓶颈=管线内 LLM 串行链（dispatcher 4.7s + fusion 4.7s），属链长问题非并发机制问题。
- 压测脚本姿势：httpx.AsyncClient 全异步 + 服务端 session id + asyncpg 前置数据。

## 安全红线

1. 只操作 student03 自己的家目录与自己的 docker daemon。
2. 甲状腺方向无可商用公开数据集（TN3K/TG3K 为 NC、TNSCUI 为 ND）——真机只用可商用数据或自造数据。
3. 密码只存 ~/uai/.smoke_pw（600），不进命令行/仓库。

## 详细档案

- 完整踩坑叙事与历史数据：[reference.md](reference.md)
- 项目进展与口径决策：docs-for-guiding generating docs/超声诊疗决策支持系统/16-开发进展汇报-20260928.md（A.2~A.10）
- 技术升级规划与状态：同目录 15-技术升级规划-202609.md
