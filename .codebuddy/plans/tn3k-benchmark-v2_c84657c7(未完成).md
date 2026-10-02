---
name: tn3k-benchmark-v2
overview: 按官方评测协议重做底座对比（v2）：EchoCare 切换到其官方唯一认可口径——全量微调（冻结探针作废）；每模型原生预处理（查 EchoCare eval 脚本的 transform）；≥3 随机种子报 mean±std + Youden 阈值；补 ULC 文本塔 I2T 检索评测（其设计任务）；先抽样人工核验 TN3K 标签正确性。产出修正版三方对比结论（可能再次反转）。
todos:
  - id: audit-p0-fixes
    content: 修复审计 P0-2/3/5：JWT→nav token 交换端点、consent 撤回去重+补 withdrawn_at、AlertEscalationManager lifespan 启停，各配单测；用 [subagent:code-explorer] 定位 AuthTokenManager 与 consent 投递链全部触点
    status: completed
  - id: audit-p0-edge
    content: 修复审计 P0-4：edge 设备 register/heartbeat/health/ota 契约对齐（后端补三端点+设备头认证），消除假隔离并验证
    status: completed
    dependencies:
      - audit-p0-fixes
  - id: audit-p1-infrastructure
    content: 修复审计 P1-1/2/7/8：脱敏频道名统一、审计链周期校验启动、compose 主机名与 redis db 显式化、阈值更新流闭环
    status: completed
    dependencies:
      - audit-p0-fixes
  - id: audit-p1-observability
    content: 修复审计 P1-3/6：obs 指标生产者接线（assessment_latency/hallucination_rate/retrieval_empty_rate/CacheMonitor）、retry_skip_self_rag 读取方生效
    status: completed
    dependencies:
      - audit-p0-fixes
  - id: audit-p2-hygiene
    content: 审计卫生项：DI 死代码与死 app.state 清理、游离白名单删除、孤儿 settings+validator 补校验、DLQ 出口、无订阅频道处置、SAD ADR-4 修正
    status: in_progress
    dependencies:
      - audit-p1-infrastructure
      - audit-p1-observability
  - id: audit-regression-deploy
    content: 全量回归（sg/sec/llm 套件）+ commit + push（GIT_LFS_SKIP_PUSH=1）+ git archive 同步服务器 + 重启主服务五端点核验
    status: pending
    dependencies:
      - audit-p2-hygiene
  - id: echocare-integration
    content: EchoCare 权重 hf-mirror 下载落服务器 + GitHub 许可证核实 + MONAI encoder 加载适配（pop mask_token）+ 特征提取冒烟
    status: pending
  - id: thyroid-data-pipeline
    content: TN3K 盘点（标签形态/良恶性覆盖/mask 抽样）+ 小规模（100~300 张）数据管线脚本跑通
    status: pending
    dependencies:
      - echocare-integration
  - id: thyroid-smoke-train
    content: GPU 窗口内双底座（EchoCare/BiomedCLIP）T2a 式冒烟训练对比 + 16 号文档追加 A.17 结论（含正式任务形态与数据充足性建议）
    status: pending
    dependencies:
      - thyroid-data-pipeline
---

## 背景与目标

用户对底座对比测试方法论提出系统性质疑，经联网核实**质疑成立**：EchoCare 官方论文（arXiv:2509.11752）只做全量微调（无 linear probing）、统计协议为 20 次重复实验 mean±std + t 检验、甲状腺阈值用 ROC Youden 指数；TN3K 官方预处理是 channel-dimension 归一化（非 ImageNet mean/std）；ULC 只测了 visual 塔绕过了其文本图融合核心设计。v1 协议数字（BiomedCLIP 0.7677 > ULC 0.7006 > EchoCare 0.6842）将标废重测。

## Gate 现状（截至本轮）

- Gate 0/0A：PASS（口径 A：88.72%/0.0%/1÷780）
- Gate 1：核心过（检索 98%、降级路径真机全过）
- Gate 2：或分支 PASS（bge-m3 Top10 98.53% ≥88%，报告已落盘；I2T 分支未达，不阻塞）
- Gate 0B：挂起（UDT 739 条医生勾选、临床队列数据、院内微调——管理/医学侧）

## 系统开发遗留（Gate 0B 与工程收口，非本计划范围）

核心覆盖率 86.3→90%（真机项）、faithfulness 68.96% 收敛、graph_query 已收口待启用评估、注册/NMPA 文档四件套、ICD 映射、前端重设计后会话契约对齐、脱敏 PII NER 另案。

## 本计划目标（v2 基准，按官方协议重测）

1. 数据正确性核验：TN3K 标签 0/1 语义从源 repo（haifangong/TRFE-Net）确认，抽样人工核对 20 张图-标签-mask 一致性
2. 每模型按**官方适配口径**分臂评测：BiomedCLIP=linear probe（其官方协议）+微调臂；EchoCare=**全量微调**（其唯一官方口径）+原生预处理；ULC=visual 探针 + **文本塔 I2T 检索**（其设计任务，纯 CLIP 文本塔不含 graph fusion 为基础臂）
3. 统计合规：≥3 随机种子 mean±std、ROC Youden 阈值、AUC+F1、与 v1 差异记录
4. 结论落盘：修正版三方对比 + Gate 2 附带条件最后确认（各器官 ≥150 文档）

## 技术方案

- **协议来源**：EchoCare 论文（arXiv:2509.11752）+ 其 GitHub eval 脚本（CAIR-HKISI/EchoCare，下游 transform/特征层/微调超参以代码为准）；TN3K 源 repo（haifangong/TRFE-Net-for-thyroid-nodule-segmentation）确认标签语义与 channel 归一化；BiomedCLIP 官方协议 = linear probe（其原生 open_clip transform 已由 8003 正确提供）。
- **评测矩阵**（TN3K 官方划分 2879/614）：
- 臂1 BiomedCLIP linear probe（8003 特征，v1 复测 + 多种子）
- 臂2 BiomedCLIP visual 微调（t2a 配方：锁文本塔、lr 5e-6、bf16、ClipLoss）
- 臂3 EchoCare 全量微调（官方唯一口径；MONAI Swin，原生 transform 以 GitHub eval 脚本为准；GPU 显存 ~8-12GB 需停判官窗口）
- 臂4 ULC visual 探针（v1 复测多种子）
- 臂5 ULC 文本塔 I2T（US-365K test 子样 500 对，base_clip.text 权重 + 双塔相似度，I2T@10；graph fusion 不含并明确标注）
- **统计**：每臂 3 种子（EchoCare 微调 1-2 次受 GPU 窗口限制时明示），mean±std，Youden 阈值取自 train ROC，报告 AUC/F1/BalancedAcc。
- **GPU 窗口**：复用 t2b_cactus_run.sh 序列（pkill 判官单独发、EngineCore 连杀、恢复后探活）；预计 EchoCare 微调 30-60 分钟、BiomedCLIP 微调 15-30 分钟。
- **工具容错**：write_to_file 间歇故障，长脚本一律 execute_command + here-string（[System.IO.File]::WriteAllText，UTF8 无 BOM）分段写入。

### SubAgent

- **code-explorer**
- Purpose: 定位 EchoCare GitHub 评测脚本（本地 clone 后）中下游 transform/特征层/微调超参，与 TN3K 源 repo 的标签语义与预处理定义
- Expected outcome: 每模型"官方口径"的精确配置清单（预处理参数、微调超参、评估指标实现），供 v2 harness 直接采用