---
name: tech-upgrade-completion-剩余项全清
overview: 完成 15 号技术升级规划全部剩余项：P0-4 依赖安全清理（附录 D 机械替换）、P0-5 P0 安全项验证、P0-2 约束解码 780 例重测收尾、P0-3 判官验收、Langfuse 接入激活、评测并发 bug 修复与 Self-RAG 重试降级、KB 语料扩展 liver/cardiac、alembic 迁移文件、15 号文档修正，全部测试绿后分批提交。
todos:
  - id: explore-contracts
    content: 用 [subagent:code-explorer] 探查 780 例评测脚本入口与数据集、obs 端点认证范围、t2c_eval workers 推导、langfuse .env 键名
    status: completed
  - id: p04-deps
    content: P0-4：按附录 D 机械替换 jose→PyJWT、清 passlib 挂名、更新守卫测试，跑 sec/quality 子集
    status: completed
  - id: alembic-selfrag
    content: 新增 alembic 迁移（embedding vector(1024)）；Self-RAG 零分即降级修正 + 单测更新
    status: completed
  - id: p05-verify
    content: P0-5 验证：obs 认证与 DICOM fail-closed 安全域回归全绿；EngineCore 处置固化进重启脚本
    status: completed
    dependencies:
      - p04-deps
  - id: langfuse-on
    content: Langfuse 激活：.env 配 OTLP 端点与密钥，重启主服务，确认 trace 上报可见
    status: completed
    dependencies:
      - explore-contracts
  - id: eval-concurrency
    content: 修 t2c_eval 并发未生效 bug（workers 推导），验证日志显示并发 6
    status: completed
    dependencies:
      - explore-contracts
  - id: kb-v110
    content: 用 [skill:remote-gpu-server-devtest] 扩展 KB：对话数据产 liver/cardiac 文档，建 kb_v1.1.0 灌数，三器官检索冒烟
    status: completed
    dependencies:
      - explore-contracts
  - id: p02-780-retest
    content: 用 [skill:remote-gpu-server-devtest] 跑 780 例一致性重测（guided decoding），对照 95% 门控如实出数归因
    status: completed
    dependencies:
      - p05-verify
      - langfuse-on
      - kb-v110
  - id: p03-faithfulness
    content: 用 [skill:remote-gpu-server-devtest] 跑 faithfulness（Qwen3-8B 判官）+ T1c 子集人工抽检相关性对照
    status: completed
    dependencies:
      - p02-780-retest
  - id: docs-final-commit
    content: 15 号修正（llama.cpp 弃用+状态落账）、16 号 A.10、SKILL 回写；全量测试绿 + pip-audit + 分批提交
    status: completed
    dependencies:
      - p03-faithfulness
---

## P0-2 780 例评测性能修订（2026-10-01 调研 vLLM 官方 Optimization 文档后）

### 根因（官方文档对照）

- vLLM 8002 启动参数仅 `--gpu-memory-utilization 0.40`：48GB 卡只给 19GB，权重 15GB 后 **KV cache 仅 ~4GB** → 8 并发立即触发 Preemption（RECOMPUTE 重算风暴）→ aggregate 61 tok/s（GPU 100% 但全在做无用功）
- `max_num_batched_tokens` 未设置（官方：最佳吞吐 >8192，示例 16384）
- prefix caching 未显式开启（780 例共享系统前缀，prefill 可大幅复用）

### 修订步骤

1. 杀当前 780 评测进程
2. 重启 vllm_server.py 8002：`--gpu-memory-utilization 0.80 --max-num-batched-tokens 16384 --enable-prefix-caching`（嵌入服务 6.3GB + 0.80×48=38.4GB ≈ 45GB < 49GB，安全）
3. 重跑 780 例（--no-guided 口径 + max_tokens=64），预期 aggregate >600 tok/s，全程 5-10 分钟
4. 归档 p02_780_vllm_nonguided.json，对照 95% 门控如实出数
5. 引擎参数固化进 vllm_server.py 默认值 + SKILL 回写

### 后续不变

- P0-3 faithfulness 重跑（KB v1.1.0 + content 接线后）
- 文档收口（15 号修正 + 16 号 A.10 + SKILL）+ 全量测试 + 提交