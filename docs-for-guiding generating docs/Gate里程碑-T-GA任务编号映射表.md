# Gate 里程碑 ↔ T-GA 任务编号 双向映射表

> **建立日期**：2026-06-30
> **来源文档**：
> - 设计侧（Gate 里程碑）：`docs-for-guiding generating docs/超声诊疗决策支持系统/06-开发环境与进度规划.md` §4
> - 工程侧（T-GA 任务卡）：`docs-for-guiding generating docs/开发计划/开发计划.md`
> **目的**：消除审查报告中"任务编号体系不对齐"项（P3-跨-01）

---

## 一、设计侧 Gate 里程碑

| Gate | 时间窗口 | 主要验收 |
|---|---|---|
| Gate 0 启动 | T0 | 项目启动 + 30 天内须完成 Ultrasound-CLIP 商业授权意向书面确认 |
| Gate 0A-Tech-P0 | Day 1-7 | LLM 选型验证（推理延迟 <3s，矛盾检测阈值校准） |
| Gate 0A-Tech-P1 | Day 7-14 | Ultrasound-CLIP 跨模态检索验证 + bge-m3 纯文本检索验证 + BM25 独立分词检索召回率验证（≥85% Top10） |
| Gate 0A-Tech-P2 | Day 14-21 | 端到端对比 + bge-m3 方案架构适配验证 + α 值校准 |
| Gate 0A 完成 | Day 21+ | Ultrasound-CLIP 与 bge-m3 端到端对比验证 + 知识库构建模型 |
| Gate 0B | — | 基线准确率实测（≥200 例，偏差 ≤±3%）+ Ultrasound-CLIP 院内微调 + UDT Phase 1（甲状腺/肝脏/心脏）审定完整 |
| Gate 1 | 2026-06-26 | 甲状腺子阶段 A：RAG 检索召回率 ≥90%，分层校验拦截率 ≥98% |
| Gate 2 | 2026-08-04 | 肝脏+心脏子阶段 B：Ultrasound-CLIP I2T@10 ≥45%（微调后）或 bge-m3 Top10 ≥88%；各器官 ≥150 份文档 |

## 二、工程侧 T-GA 任务卡

| 任务编号 | 轨道 | 状态（2026-06-30） |
|---|---|---|
| T-GA-DATA-01 | DATA 知识库构建管线 | ✅ 已完成（commit bc571ef） |
| T-GA-EMB-01 | EMB 嵌入服务 | ✅ 已完成（含 Qdrant/PGVector 双后端） |
| T-GA-EMB-02 | EMB 向量索引工厂 | ✅ 已完成（含 Qdrant→PGVector 自动降级） |
| T-GA-RAG-01 | RAG 查询增强 | ✅ 已完成（3 层 UDAF + 可选 LLM） |
| T-GA-RAG-02 | RAG 多模态并行检索 | ✅ 已完成（Dense+Sparse+Image+RRF） |
| T-GA-RAG-03 | RAG 重排序 + BM25 降级 | ✅ 已完成（修复 BM25 装配断链） |
| T-GA-FUS-01 | FUS 融合层 | 🔄 进行中 |
| T-GA-AGT-Foundation | AGT 智能体基座 | 🔄 进行中 |
| T-GA-AGT-Specialists | AGT 专科智能体 | 🔄 进行中 |
| T-GA-LLM选型 | LLM 推理引擎 | ✅ 已完成 |
| T-GA-INT集成 | 集成与回归 | 待启动 |

## 三、双向映射

| Gate 里程碑 | T-GA 任务 | 状态 | 备注 |
|---|---|---|---|
| Gate 0A-Tech-P0 | T-GA-LLM选型 | ✅ | LLM 选型验证 |
| Gate 0A-Tech-P1 | T-GA-EMB-01/02 + T-GA-DATA-01 | ✅ | 嵌入服务 + 知识库构建管线 |
| Gate 0A-Tech-P2 | T-GA-RAG-01/02/03 | ✅（修复中） | RAG 全链路 |
| Gate 0A 完成 | T-GA-EMB + T-GA-RAG + T-GA-DATA | ✅ | 端到端对比验证 |
| Gate 0B | T-GA-EMB-01 微调 + T-GA-DATA-01 扩展 | 🔄 | 基线准确率 + UDT Phase 1 审定 |
| Gate 1 | T-GA-RAG-01/02/03 + T-GA-FUS-01 + T-GA-AGT-Foundation | 🔄 | 甲状腺子阶段 A |
| Gate 2 | T-GA-EMB-01 微调 + T-GA-DATA-01 肝脏/心脏 + T-GA-AGT-Specialists | 🔄 | 肝脏+心脏子阶段 B |

## 四、未映射项与延期声明

| 设计要求 | 归属 | 延期 | 备注 |
|---|---|---|---|
| Self-RAG 质检 + CRAG 检索纠错 | T-GA-AGT-Foundation | 待排期 | 审查报告 P1-RAG-03 |
| GNN + 规则融合 | T-GA-EMB 二期 | 二期 | 审查报告 P1-EMB-04 |
| PGVector 递归 CTE 图谱查询 | T-GA-DATA 二期 | 二期 | 审查报告 P1-KB-03（已标注 STUB） |
