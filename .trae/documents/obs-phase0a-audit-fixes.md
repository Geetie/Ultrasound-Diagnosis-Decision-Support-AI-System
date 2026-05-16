# OBS可观测性轨道 Phase0A 审查问题修复计划

## 修复项总览

| # | 优先级 | 问题 | 涉及文件 |
|---|---|---|---|
| 1 | P0 | CUSUMMonitor月度告警回调使用类变量 | `app/observability/cusum/monitor.py` |
| 2 | P0 | 熔断器PENDING状态无主动通知 | `app/observability/cusum/circuit_breaker.py` |
| 3 | P1 | Webhook适配器无测试 | `tests/unit/obs/test_webhook_adapter.py` (新建) |
| 4 | P1 | AlertEscalationManager端到端升级链路无集成测试 | `tests/unit/obs/test_obs_integration.py` (追加) |
| 5 | P1 | LogStorage compact模式无降级后备 | `app/observability/logging/storage.py` |
| 6 | P1 | AlertContact模型字段语义与设计文档不一致 | `app/observability/alert_escalation/models.py`, `contacts.py` |

---

## Step 1: P0-1 — CUSUMMonitor._alert_escalation_callback 从类变量改为实例变量

**问题**：`_escalate_monthly_alert` 访问 `CUSUMMonitor._alert_escalation_callback`（类变量），导致所有 CUSUMMonitor 实例（含病种级监控器）共享同一个回调，病种级月度告警会误触发全局回调。

**修改方案**：
1. 删除类变量声明 `_alert_escalation_callback: Optional[Callable] = None`（如果存在的话——当前代码中它其实是在 `__init__` 中作为 `self._alert_escalation_callback` 初始化的，但 `_escalate_monthly_alert` 方法中用 `CUSUMMonitor._alert_escalation_callback` 类变量方式访问）
2. 将 `_escalate_monthly_alert` 中的 `CUSUMMonitor._alert_escalation_callback` 改为 `self._alert_escalation_callback`
3. 将 `register_alert_escalation_callback` 从 `@classmethod` 改为实例方法，操作 `self._alert_escalation_callback`
4. 更新调用方：`AISafetyCircuitBreaker` 中如有通过类方法注册的地方需改为实例方法注册

**验证**：运行 `pytest tests/unit/obs/test_cusum.py -v`，确保现有测试通过；新增测试验证病种级监控器月度告警不触发全局回调。

**文件变更**：
- `app/observability/cusum/monitor.py`：修改 `_escalate_monthly_alert`、`register_alert_escalation_callback`
- `tests/unit/obs/test_cusum.py`：追加病种级回调隔离测试

---

## Step 2: P0-2 — 熔断器PENDING状态增加主动通知

**问题**：设计文档 §159 要求"触发前通知科室主任确认"，但 `_request_suspension` 进入 PENDING_CONFIRMATION 时仅记录日志，无主动推送。

**修改方案**：
1. 在 `AISafetyCircuitBreaker.__init__` 中新增 `_pending_notification_callbacks: List[Callable]` 列表
2. 在 `_request_suspension` 中，进入 PENDING_CONFIRMATION 分支后（锁外），调用 `_notify_pending_confirmation(reason)` 方法
3. `_notify_pending_confirmation` 遍历 `_pending_notification_callbacks`，每个回调接收 `(reason, confirm_deadline_seconds)` 参数
4. 新增 `register_pending_notification_callback(callback)` 实例方法
5. 在集成层（`app/observability/__init__.py` 或启动代码中），将 `AlertEscalationManager.emit_alert` 注册为 pending 通知回调，发出 `level="critical"` 告警

**验证**：运行 `pytest tests/unit/obs/test_cusum.py tests/unit/obs/test_obs_integration.py -v`

**文件变更**：
- `app/observability/cusum/circuit_breaker.py`：新增回调列表、通知方法、注册方法
- `tests/unit/obs/test_cusum.py`：追加 PENDING 通知回调测试
- `tests/unit/obs/test_obs_integration.py`：追加 PENDING→告警联动集成测试

---

## Step 3: P1-3 — 补充 Webhook 适配器单元测试

**修改方案**：
新建 `tests/unit/obs/test_webhook_adapter.py`，覆盖：
1. `_format_alerts_text` — 空告警列表、正常告警、含 summary 告警
2. `_build_feishu_payload` — CRITICAL 红色模板、WARNING 橙色模板
3. `_build_dingtalk_payload` / `_build_wecom_payload` — 基本结构验证
4. `feishu_webhook` 端点 — 未配置时 503、正常请求 200、无效 JSON 400
5. `dingtalk_webhook` 端点 — 同上
6. `wecom_webhook` 端点 — 同上
7. `health` 端点 — 返回 ok

使用 `httpx.AsyncClient` + `ASGITransport` 进行 FastAPI TestClient 风格测试，mock 环境变量。

**验证**：运行 `pytest tests/unit/obs/test_webhook_adapter.py -v`

**文件变更**：
- `tests/unit/obs/test_webhook_adapter.py` (新建)

---

## Step 4: P1-4 — 补充 AlertEscalationManager 端到端升级链路集成测试

**修改方案**：
在 `tests/unit/obs/test_obs_integration.py` 中追加 `TestAlertEscalationE2E` 类，覆盖：
1. Warning 告警 → 确认超时提醒 → 升级至上级医院完整流程
2. Critical 告警 → 15分钟未确认升级 → 1小时升级至行政+供应商完整流程
3. Critical 告警确认后不升级

使用短超时参数（confirm_seconds=1, escalate_seconds=2）加速测试，mock webhook 发送。

**验证**：运行 `pytest tests/unit/obs/test_obs_integration.py -v`

**文件变更**：
- `tests/unit/obs/test_obs_integration.py` (追加测试类)

---

## Step 5: P1-5 — LogStorage compact 模式增加 FileLogStorage 降级后备

**问题**：compact 模式仅使用 PostgreSQLLogStorage，PostgreSQL 不可用时无降级方案。

**修改方案**：
1. 在 `LogStorage.__init__` 中，compact 模式下将 `FileLogStorage` 作为 `BatchLogStorageWrapper` 的降级后备
2. 新增 `_create_compact_backend` 方法：先尝试创建 PostgreSQLLogStorage，如果初始化失败则降级为 FileLogStorage
3. 降级时记录 WARNING 日志，说明 PostgreSQL 不可用已降级为文件存储
4. 不改变 full 模式行为

**验证**：运行 `pytest tests/unit/obs/test_logging.py -v`；新增降级路径测试。

**文件变更**：
- `app/observability/logging/storage.py`：修改 `__init__` 中 compact 分支
- `tests/unit/obs/test_logging.py`：追加降级路径测试

---

## Step 6: P1-6 — AlertContact 模型字段语义对齐设计文档

**问题**：设计文档 §203 要求联系人包含"上级医院运维联系方式、医院行政值班联系方式、设备供应商技术支持热线"，当前 `AlertContact` 将这些作为同一联系人的属性字段，但设计意图是这些应为独立联系人角色。

**修改方案**：
1. 保留 `AlertContact` 现有字段不变（向后兼容）
2. 在 `contacts.py` 的 `_build_contact` 中增加角色映射逻辑：当 `role` 为 `superior_hospital_ops` / `hospital_admin_duty` / `equipment_supplier` 时，自动填充对应字段
3. 在 `_escalate_critical_level2` 中，按 `role` 筛选联系人时，同时检查 `superior_hospital_ops` / `hospital_admin_duty` / `equipment_supplier` 字段非空的联系人
4. 新增测试验证角色映射和升级通知目标选择

**验证**：运行 `pytest tests/unit/obs/test_alert_escalation.py -v`

**文件变更**：
- `app/observability/alert_escalation/contacts.py`：增强 `_build_contact`
- `app/observability/alert_escalation/manager.py`：增强 `_escalate_critical_level2` 联系人筛选
- `tests/unit/obs/test_alert_escalation.py`：追加角色映射和升级目标测试

---

## Step 7: 全量回归测试

运行完整 OBS 测试套件，确保所有修改不引入回归：

```bash
pytest tests/unit/obs/ -v --tb=short
```

验证 259 + 新增测试全部通过。

---

## 执行顺序

1. Step 1 (P0-1) → 验证: `pytest tests/unit/obs/test_cusum.py -v`
2. Step 2 (P0-2) → 验证: `pytest tests/unit/obs/test_cusum.py tests/unit/obs/test_obs_integration.py -v`
3. Step 3 (P1-3) → 验证: `pytest tests/unit/obs/test_webhook_adapter.py -v`
4. Step 4 (P1-4) → 验证: `pytest tests/unit/obs/test_obs_integration.py -v`
5. Step 5 (P1-5) → 验证: `pytest tests/unit/obs/test_logging.py -v`
6. Step 6 (P1-6) → 验证: `pytest tests/unit/obs/test_alert_escalation.py -v`
7. Step 7 → 验证: `pytest tests/unit/obs/ -v --tb=short`
