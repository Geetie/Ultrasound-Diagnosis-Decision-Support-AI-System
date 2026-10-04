---
name: remaining-tasks-closeout
overview: 收尾两快一主：①Gate 2 附带条件确认（各器官≥150 source_doc，SQL 一发）；②I2T 97.8% 去重复核（同病例近重复占比→去重后口径）；③核心覆盖率 86.3→90%（真机覆盖率报告→按缺口补测试→服务器 SoftHSM/Redis 真机用例）。stretch：ULC graph-fusion 拼装、EchoCare 分割。
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

## 收尾任务（接 v2 底座对决之后）

用户指令：继续完成剩余任务。四项：

1. **Gate 2 附带条件确认**（快）：官方口径“各器官 ≥150 文档”——对 kb_v1.3.0 的 source_doc 按器官计数，一 SQL 确认 → Gate 2 完整 PASS 声明落盘
2. **I2T 97.8% 去重复核**（快）：US-365K test 500 对中 caption 近重复/同病例多图占比分析 → 去重后重算 I2T@10，给出可信口径（97.8% 疑含同病例多图近重复）
3. **核心覆盖率 86.3→90%**（主体）：真机/本地跑 `pytest --cov=app/{security,services,fusion,agents}` 带行号报告 → 按未覆盖行补测试 → SoftHSM 签名字节/真实 Redis(16379)/GPU 真机用例在服务器执行；顺带排查存量失败 test_softhsm_active_within_90_days（KeyError 'status'）
4. **stretch（视窗口余量）**：ULC graph-fusion 文本侧拼装（6 组件参考实现 + vocab_v2 兜底 tag 词表）后重测 I2T；EchoCare TN3K 分割评测

完成后 A.20 落 16 号文档 + commit/push/服务器同步。

## Gate 现状

- Gate 0/0A：PASS（口径 A）｜Gate 1：核心 PASS｜Gate 2：或分支双 PASS（Top10 98.53% + I2T 97.8%，后者待去重复核确认）｜Gate 0B：挂起（医学/管理侧：UDT 739 条医生勾选、临床队列数据、院内微调）

## 技术方案

1. **Gate 2 附带条件**：asyncpg 直查 `SELECT organ, COUNT(DISTINCT source_doc_id) FROM knowledge_chunks WHERE kb_version='kb_v1.3.0' GROUP BY organ`，对比 ≥150 门槛，结果并入 gate2 报告 JSON。
2. **I2T 去重复核**：解析 reports/ulc_i2t_report.json 对应的 500 对样本——①caption 精确重复计数；②image 文件名前缀（病例号，如 0613-1 病例 ID）分组统计同病例多图占比；③caption 归一化后近似重复（去空白/小写）分组；④去重后（每组仅保留 1 对）重算 I2T@10。若图像文件已下载在本地 data/us365k/images 可直接复用 basename 索引。
3. **覆盖率攻坚**：

- 本地跑 `pytest tests/unit --cov=app/security --cov=app/services --cov=app/fusion --cov=app/agents --cov-report=term-missing` 拿行级缺口；
- 缺口分类：纯逻辑未测（本地补单测）/ 需真机（SoftHSM 签名字节、真实 Redis 16379、GPU）→ 写 pytest 真机标记用例（服务器跑）；
- 排查存量失败 test_softhsm_active_within_90_days（KeyError 'status'）——大概率是 HSM 状态字典字段变更未同步，属可修复项；
- 目标核心 ≥90%（CI 门禁 fail_under=86 是整体，核心 90 是 Gate 记分牌口径）。

4. **统计与落盘**：全部结果落 reports/，A.20 追加 16 号文档，commit/push（GIT_LFS_SKIP_PUSH=1），git archive 同步服务器。

## 环境铁律（延续）

- worktree e:\uai-feature（feature 分支 5e66305）；主工作区 main 被前端窗口占用
- 服务器 fish：本地写脚本→scp→bash；write_to_file 间歇故障→长内容走 execute_command + here-string（UTF8 无 BOM）
- pkill -f 脚本名自杀坑用 [c] 字符类；判官 8006 配比 0.40 / 8002 0.46（scripts/window_judge_{stop,start}.sh 可复用）
- 推送 GIT_LFS_SKIP_PUSH=1；服务器 /tmp 探针用完即清；uai-postgres 15432 是我们的（6379 redis 是别人的）