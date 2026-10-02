# 软件架构设计说明书（SAD）

> **状态：框架（骨架）**。架构与分层以**代码实际结构**为准，本文件做结构化说明与
> 设计决策记录（ADR 摘要），不描述尚未实现的组件。
>
> 相关：`docs/合规/发布就绪报告.md`（门禁与实测）、`覆盖率豁免清单.md`（真机项边界）。

## 1. 系统分层（与代码目录一一对应）

```
接入层     app/api/v1/*（16 个路由模块，含 fhir / report / speech / obs_dashboard）
           app/api/websocket/*（导航实时通道、双连接管理）
服务/管线层 app/services/*（诊断管线、报告、审计、保留策略、灰度路由、遥测客户端）
            app/rag/*（检索与缓存）、app/knowledge/*（知识库构建/衰减/规则引擎）
智能体/融合 app/agents/*（中央调度、三专科智能体 thyroid/liver/cardiac、监控）
            app/fusion/*（冲突检测、一致性、Self-RAG、CRAG、脱敏）
安全网关   safety_gateway/*（独立进程：禁忌校验、降级输出、同意直查 PG、熔断）
            app/security/*（三层脱敏、HSM、同意撤回消费、审计链、应急签名）
推理与嵌入 app/inference/*（llama.cpp / vLLM 适配器、热重载、漂移、GPU 监控）
            app/embedding/*（bge-m3、超声 CLIP、Qdrant / pgvector 客户端）
边缘端     edge/*（DICOM SCP、导航推理、切面/质量/测量适配器、OTA、设备管理）
前端       frontend/src/*（9 个页面 + 诊断组件族 + 导航浮层 + 报告页）
观测       app/observability/*（结构化日志、指标采集、CUSUM、看板接口）
```

## 2. 关键设计决策（ADR 摘要）

| 决策 | 内容 | 理由 |
|---|---|---|
| ADR-1 契约先行 | 先统一扩展 `AgentResponse` / `DiagnosisResult` / 导航标注契约，再分批填字段 | 避免多批次反复改同一批共享文件导致既有测试轨道反复变红 |
| ADR-2 零迁移优先 | 展示型/统计型新字段写 JSONB `findings`，仅索引/聚合需求才加迁移 | 降低链路冲突；当前迁移 head 已推进到 0012（知识衰减） |
| ADR-3 真机能力适配器化 | TensorRT 导航、图像质量评分、尺寸换算、图查询引擎等以 `Protocol` + 本地确定性假实现交付 | "可离线完成"与"验收口径"不冲突；真机实现由现场注入 |
| ADR-4 白名单三处同口径 | 导航标签/字段白名单在边缘端、安全网关、脱敏层各一份，用一致性测试锁定 | 防止安全口径漂移 |
| ADR-5 安全网关直查 PACS/PG | 同意状态**不降级为 Redis 缓存**；PG 不可用时拒绝输出（E4005B） | 宁可不出结果，不可基于失效同意出结果 |
| ADR-6 审计链不可篡改 | 每条记录 `hash_prev` 链接上一条 `hash_self`，detail 按 key 排序参与哈希 | 断链即可定位篡改点；排序哈希避免"同内容不同哈希"导致无法判定 |
| ADR-7 脱敏失败开放（待确认） | DICOM 解析失败时返回原始字节且照常保存 | 现状；是否改为"失败拒存"待需求确认（见 SRS 第 6 节） |

## 3. 数据流（诊断主链路）

```
边缘端导航推理 → 白名单校验 → 后端导航接入(WebSocket)
   → risk_flag=high 触发自动升级 → 诊断管线
   → 专科智能体 → 融合层 → 安全网关 → 结果落库(findings)
   → 前端结果页 / 报告页 / 观测指标
```

**前端链路约束（本次修复）**：结果页**必须**由提交方通过 `?request_id=` 携带请求标识
进入，否则无从取数；该契约已由 `frontend/src/pages/__tests__/DiagnosisFlowWiring.test.ts`
源码级守卫钉住。

## 4. 接口与协议

- 外部接口规范见 `03-接口规范.md`；本系统实现位于 `app/api/v1/*`。
- WebSocket：导航实时通道（`app/api/websocket/nav.py`）与前端双连接管理器。
- 报告接口：`app/api/v1/report.py`（草稿 → 签名 → 推送 → 校验四段，均写审计）。

## 5. 横切关注点

| 关注点 | 实现位置 | 说明 |
|---|---|---|
| 配置 | `app/config/settings.py` + `validator.py` | 新增配置必须配校验器并在启动期校验 |
| 日志 | `app/observability/logging/*` | structlog 结构化日志 + 文件后端 |
| 指标 | `app/observability/metrics_collector/*` | Prometheus 指标；看板读取侧有守卫（防"写入正常、读取静默失败"） |
| 审计 | `app/services/audit_service.py` | 前向哈希链 + 周期校验任务 |
| 事件循环纪律 | 各模块单例 | 模块级单例必须绑定当前事件循环（历史上修过同类死锁） |

## 6. 待补章节（需人工完成）

1. 部署视图（`deploy/` 的实际拓扑与资源规格）与容量规划。
2. 故障模式与影响分析（FMEA）——需与风险分析文档联动。
3. 序列图：诊断主链路、同意撤回全链路、报告签发与推送。
4. 软件安全分级对应的架构措施追溯（对应 SRS 第 6 节第 4 项）。
