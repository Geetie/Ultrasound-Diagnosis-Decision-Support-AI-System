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
6. 开工先对齐项目状态（仓库根 `progress.md` / `feature_list.json`），真机实测数字收尾回写 `16` 号附录 A。

## 与项目 harness 状态对齐

| 文件 | 用途 |
|---|---|
| `AGENTS.md` | Agent 入口：硬规则（含服务器禁则）、DoD、验证命令 |
| `progress.md` | 当前目标 / 下一步 / 阻塞（服务器侧改动后回写） |
| `feature_list.json` | 功能与门控状态 + 证据指针（真机评测对上门控项） |
| `docs-for-guiding generating docs/超声诊疗决策支持系统/16-开发进展汇报-20260928.md` 附录 A | 真机实测数字与证据台账（只追加） |
| SKILL `uai-harness-maintenance` | 文档/技能维护规范（何时回写、写在哪） |

## 环境事实

| 项 | 值 |
|---|---|
| GPU | RTX 5880 Ada 48GB（单卡 index 0）；nvcc 12.4 |
| OS / Python | Ubuntu 24.04 / 3.12.3（无 sudo，venv 自举） |
| 家目录 | /mnt/ee16/home/student03/uai/{src,venv,venv-vllm,models,data,reports,logs} |
| 外网 | hf-mirror.com 与 pypi 可达；huggingface.co 与 docker registry 不通 |

## 服务拓扑（2026-10-02）

| 端口 | 服务 | 启动/探活 |
|---|---|---|
| 18000 | 主 API（FastAPI） | /api/v1/health；重启见 reference.md R1 |
| 8002 | vLLM 官方 OpenAI server（Qwen2.5-7B-Instruct）＝ 主生成 | /health；命令见下 |
| 8003 | 图像嵌入（BiomedCLIP 底座）+ T2a/T2b 端点 | bash ~/server_start_ultrasound_clip.sh |
| 8004 / 8005 | bge-m3 / bge-reranker | bash ~/server_start_embedding_services.sh |
| 8006 | 判官 Qwen3-8B（GGUF Q4_K_M，llama_cpp_server.py，评测用 /no_think） | bash ~/server_start_judge.sh |
| 8007 | 双引擎评测临时实例（Qwen2.5-1.5B vLLM，仅 Gate 0A 重测期） | 临时起，评测后停 |
| 8001 | ~~llama.cpp~~（2026-10-01 已弃用，compact 档链路验证才起） | `server_start_llama_server.sh`（CPU-only 包，勿作主引擎） |
| 9001 | SafetyGateway | 随 ~/server_restart_all.sh |
| 16379 / 15432 | Redis / PG 容器（`uai-redis` / `uai-postgres`） | bash ~/server_infra_up.sh |
| 30100 | Langfuse（docker 全栈） | ~/uai/langfuse/docker-compose.yml |

一键健康自检（全服务端口探活）：`bash scripts/_verify_services.sh`（在 `~/uai/src`）。

**GPU 显存账（48GB，串行启动）**：主生成 8002 0.46 + 判官 8006 0.36 + 嵌入×3 基线 6.8GB ≈ 45.4GB；
任何额外 GPU 进程（探针导出/训练）须**临时停判官**再跑。双实例**并发启动会互踩 memory probe**，必须串行。


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
  --kb-version kb_v1.3.0 [--chunk-min-tokens N --chunk-max-tokens N]
PYTHONPATH=$HOME/uai/src ~/uai/venv/bin/python scripts/server_populate_vector_index.py \
  --kb-version kb_v1.3.0 --purge
```

- 对话语料参数化：scripts/t1b_build_dialogue_docs.py（--organ/--keyword/--sources）；PDF 语料用 `build_pdf_corpus.py`（pymupdf4llm）。
- 灌数幂等；重建后必须 --purge 清旧索引行。chunk_id 含 kb_version（多版本防撞车）。
- 现状 **kb_v1.3.0（生产）**：1257 chunks（thyroid 601 / liver 313 / cardiac 343）。切库后须重启主服务并跑 `_verify_services.sh`。
- 图谱邻居扩展（`graph_query_enabled`，默认 **False**）需要边投影，用 `scripts/kb_backfill_relations.py` 回填。

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
| 双 vLLM 同卡启动互踩 | gpu_memory_utilization 是占全卡比例——并发启动各自 memory probe 失败；**串行启动**（前一个 /v1/models 就绪再启下一个），分配：基线 6.8 + 主 0.46 + judge 0.36 ≈ 45GB |
| HF 下载 Network unreachable | 校园网到 HF/mirror 全断——**先用本地已有模型**（uai/models/Qwen3-8B），AWQ 等量化方案作废 |
| pkill 与部署脚本同 ssh | 自杀老坑再现：pkill 单独发，部署脚本后台跑 |
| 部署半成品 NameError/AttributeError | 多文件改动只 scp 了部分——**跨文件改动（代码+settings）必须整组部署**；单测 mock 不触发真实 settings 加载，全绿≠部署完整。部署后先跑 1 例端到端再批量 |
| PDF 语料解析 | **pymupdf4llm**（零模型下载）：8.1s/60 页 → Markdown 段落完整+标题+双栏正确；自写分栏启发式会整段乱序勿用；CJK 断行空格用正则合并。pip 走清华源可达，HF/ModelScope 不可达 |
| open_clip 训练 | 官方 main 参数：`--csv-separator ","`（默认 TSV 的坑）、`--csv-img-key/--csv-caption-key`、`--val-data`（非 valid-data）、`--report-to tensorboard`、`--resume latest`+`--save-most-recent` 续训、`--lock-text`；CSV 必须 pandas 生成（手写引号转义会破列）；**训练慢先查 CPU 解码**（Data t/step 高=加 workers，GPU 不一定是瓶颈）；open_clip_train 需 repo 版 open_clip（pip 3.3.0 无 create_task），覆盖 site-packages 时先 mv 备份 |
| Qwen3-8B 部署/评测 | 默认思考模式输出 `<think>` 污染 JSON——请求级 `chat_template_kwargs:{"enable_thinking":false}` 关闭；思考判官实测不提升反略降（66.6% vs 69.0%）；A/B 评测口径三查：判官 max_tokens 截断（幸存者偏差）、答案长度偏倚、服务配额差异污染速度结论（单发串行才能测真实速度） |
| /tmp 探针累积 116 个 | 每轮只删本轮脚本不够——**历次会话探针持续累积**；收尾时 `ls /tmp/_*.py /tmp/_*.sh | wc -l` 全量清点一次清空（正式脚本都在 ~/uai/src/scripts/，/tmp 的 _ 前缀文件全可删） |
| FRDR 数据集下载 | `/repo/files/<path>` 跟随 302 到 data.globus.org 匿名直下；目录**无 index**，清单走 `/repo/userfiles` API（GET 五参：item_id/collection_id/endpoint_id/path/limit/offset）；文件清单以 **frdr-dfdr-checksums.txt 为权威**（README 数字会虚标——CACTUS 发布版实际缺 PSAV 5,832 张）；frdr.usask.ca 旧域名已死，用 frdr-dfdr.ca |
| Windows 端内联命令 | PowerShell 里 ssh 内联 `python -c` 多行/复杂引号必炸——**一律本地写脚本 → scp → bash 执行**（fish 铁律的 Windows 端变体）；`head` 等 Unix 命令 PowerShell 没有，用 `Select-Object` |
| CACTUS/T2b 资产 | ~/uai/data/cactus（29,963 张 5 视图 + 分级 CSV）、~/uai/data/t2b_cactus/{train,val,test}.csv（分层 21k/4.5k/4.5k）；训练 `scripts/t2b_cactus_run.sh` 一键（停双 vLLM→训练→串行恢复），需服务窗口批准 |
| KB v1.3.0 | 生产切换完成（thyroid 601/liver 313/cardiac 343 = 1257 chunks）；RAGAS golden 口径语料路线**已饱和**（+83 指南文档 A/B 实测 -1.7pp 噪声内偏负），提升空间在生产 T2c 口径与 query-context 对齐 |
| open_clip 本地权重加载 | `create_model_and_transforms("local-dir:" + 路径)`（与 8003/t2a 同款）；**路径直传 model name 会报 not found in built-ins**；open_clip_train CLI 需 repo 版库，脚本层用官方 API 即可 |
| T2b 战果 | CACTUS 切面头训练完成：test Top-1 **0.9793**（门 ≥0.75 通过；基线 0.921，体模数据偏高属预期）；质量 MAE 0.773/R² 0.765；资产 reports/t2b_cactus.{pt,json}；训练 3 epochs×150s，bs 256 显存 ~21GB（嵌入常驻下可共存，无需停嵌入） |
| Cornerstone3D 迁移终态 | **完成且构建通过**（npm run build ✓）：@cornerstonejs/* 5.11.3 全替换旧 4 包；两处尾巴已修——dicomStore 的 window.cornerstone 全局引用（CS3D 不挂全局，tsc 必炸）与适配器 setViewport 静默丢 scale/translation/invert（与 getViewport 读口不对称） |
| Gate 0A-Tech-P0 重测 | 780 例两轮口径：**guided-json 口径作废**（引擎 A 恒定输出伪影——guided 模式不追加任务指令，1.5B Q4 按语法槽位填最常见枚举；手工三问证模型正常）；**structured-output 正式口径 70.13%**（常规+高风险层 75.59%）vs ≥95% 门控 FAILED——异构精度（Q4_K_M vs bf16）跨栈一致率不可达 95%，幻觉率差 2.95% 接近达标；待项目方口径决策 |
| llama.cpp b11272 | CUDA 构建但需 **CUDA 13 运行时**（libcudart.so.13/libcublas.so.13），系统只有 CUDA 12.4 → GPU offload 静默失败回退 CPU（日志无 CUDA 行 + GPU 不涨即中招）；vLLM venv 的 `nvidia/cu13/lib/` 有 .so.13 但驱动为 12.4 代仍 "no CUDA-capable device"——**该构建在本机只能 CPU**；kill 主 vLLM 必须连 `VLLM::EngineCore` 一起杀（api_server 死后 EngineCore 存活继续占显存） |
| P99 压测定容 | `server_p99_capacity.py`：conc 5→20 P99 = 0.73→0.79s 近乎平坦、26.7rps、零错误——记分牌 #6 PASS 余量 10 倍；vLLM continuous batching 下尾延迟不随并发恶化 |
| 降级注入拓扑 | 我们的中间件是 docker 容器 `uai-redis`(16379)/`uai-postgres`(15432)——**6379 上的 redis-server 是共享服务器上别人的，绝不能动**；SG 独立 uvicorn(9001)；真机注入已验证：Redis 停机→PG 降级 1/1 ✅、SG 停机→拒绝输出 ✅、组合 4s 快拒 ✅；融合层在主进程内无法真机注入（CI 覆盖） |
| T2b 端点 | 8003 新增 `POST /v1/analyze_view`（微调 visual 副本 + LR 切面探针 + 质量回归，懒加载不污染检索权重）；探针经 `t2b_export_probes.py` 落盘 models/t2b/；真图验证 A4C@0.9991+质量 4.3、Random@0.9937+null |
| GPU 满时探针导出 | 嵌入+双 vLLM 占满 45.4GB 时任何额外 GPU 进程必 OOM——探针导出/训练类任务须临时停判官（17GB）跑完再恢复；ssh 会话内裸 python 脚本记得带 `CUDA_VISIBLE_DEVICES=0`（否则 "No CUDA GPUs are available"） |
| SG fusion 误报根因 | `sg_config env_file=".env"` 按 CWD 读 `src/.env`——`FUSION_LAYER_PORT=18000` 只在 `~/uai/.env`，SG 用默认 8000 探测必拒 → 已补 src/.env 修复（fusion healthy 8.33ms）；残留 overall degraded 由 oom_protection warning 驱动（-1000 需 root/compose），bare 进程环境产物非缺陷 |
| 图谱 CTE 真机报 `AmbiguousParameterError` | asyncpg 无法推断 NULL/数组参数类型——**显式 `CAST(... AS ...)`**（P2-KB-08 收口，commit 0eeb3cb） |
| 8003 能力端点（非仅嵌入） | `POST /v1/classify`（T2a 微调 visual 副本 + LR 探针 → 三分类概率）、`POST /v1/analyze_view`（T2b 切面 + 质量回归）；均**懒加载**独立副本，不污染基线检索权重；批量嵌入契约是 `/v1/embeddings/multimodal` |
| 评测器 fail-fast 三层 | ①模型预检——gen/judge **分端口后必须分别校验**（只查一处会误报 FATAL）；②连错熔断；③失败率 30% 退出。跑完抽查 report 的 `error` 字段 + 抽样结论非空 |
| harness 状态同步 | 真机改动收尾回写 `progress.md`/`feature_list.json` 与 16 号附录 A；维护规则见 SKILL `uai-harness-maintenance` |

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
- 项目进展与口径决策：docs-for-guiding generating docs/超声诊疗决策支持系统/16-开发进展汇报-20260928.md（**附录 A.2~A.16**，含 Gate 0A 重测 / T2a-T2c / faithfulness / UDT / 医学知识资产加固）
- 当前状态与门控：仓库根 `progress.md`、`feature_list.json`
- 技术升级规划与状态：同目录 15-技术升级规划-202609.md
- 文档与技能维护规范：SKILL `uai-harness-maintenance`
