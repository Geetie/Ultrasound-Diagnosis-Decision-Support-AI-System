# SEC安全合规轨道系统性审查报告（第二轮深度审查）

**审查日期**：2026-05-12
**审查范围**：Phase 0 ~ Phase 1 SEC轨道全部实现代码、测试逻辑与核心参考文档一致性
**审查原则**：独立、客观、深入、批判性
**核心指标**：CI≥1000条测试 ✅(1038)、签名P99<5ms ✅(有基准)、撤回后<500ms生效 ⚠️(Mock测试)

---

## 总览

| 维度 | 状态 | 问题数 |
|------|------|--------|
| 设计规范一致性 | ⚠️ 存在偏差 | P0:1, P1:3, P2:4 |
| 代码质量 | ✅ 整体良好 | P0:1, P1:2, P2:3 |
| 代码风格 | ✅ 基本一致 | P2:2 |
| 功能正确性 | ⚠️ 存在隐患 | P0:1, P1:2, P2:2 |
| 测试逻辑有效性 | ⚠️ 存在薄弱环节 | P0:1, P1:3, P2:2 |
| **合计** | | **P0:4, P1:10, P2:13** |

---

## 一、P0级问题（必须修复）

### P0-1: ConsentStatus枚举定义不一致——主服务缺PENDING，SG有PENDING

**严重程度**：P0 — 跨模块枚举不一致可能导致运行时AttributeError或比较失败

**发现**：
- [schemas.py:27-30](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/models/schemas.py#L27-L30) 定义 `ConsentStatus` 有3个值：`GRANTED, WITHDRAWN, DENIED`（**无PENDING**）
- [validation_result.py:12-16](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/validation_result.py#L12-L16) 定义 `ConsentStatus` 有4个值：`GRANTED, DENIED, WITHDRAWN, PENDING`（**含PENDING**）

**影响**：
1. `consent_checker.py:71` 在无同意记录时返回 `ConsentStatus.PENDING`，该值如果被传到使用 `schemas.py` 定义的代码中，`is` 比较会失败
2. `consent_phase.py:79` 将 `DENIED` 和 `PENDING` 同等对待（返回failed），但 `schemas.py` 一侧的代码（如 `diagnosis.py`）完全不知道PENDING状态
3. 03-接口规范.md §5.1 明确要求无记录返回403+E1008，但SG返回PENDING而非DENIED

**修复建议**：
- 方案A（推荐）：在 `schemas.py` 的 `ConsentStatus` 中增加 `PENDING = "pending"`，统一两处定义
- 方案B：将 `consent_checker.py` 中无记录时的返回值从 `ConsentStatus.PENDING` 改为 `ConsentStatus.DENIED`（对齐03-接口规范"无记录=未签署"语义）
- 无论哪种方案，都应消除两处枚举定义的不一致

---

### P0-2: 同意撤回<500ms SLA基准测试使用MockHandler，不能反映真实延迟

**严重程度**：P0 — 核心安全SLA指标缺乏有效验证

**发现**：
- [test_revocation_latency_benchmark.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/benchmark/sec/test_revocation_latency_benchmark.py) 使用 `MockSafetyActionHandler`（仅做list append），而非真实 `SafetyActionHandler`
- 真实Handler涉及：PG查询+更新、Redis set/publish/scan_iter、审计日志写入——这些操作的实际延迟未被基准测试覆盖
- [test_sec_p0_critical.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/sec/test_sec_p0_critical.py) 中 `test_consent_withdrawal_marks_session_within_500ms` 使用真实Handler但仅单次调用，无P99统计意义

**影响**：无法确认生产环境下同意撤回<500ms SLA是否真正达标

**修复建议**：
1. 在基准测试中引入真实 `SafetyActionHandler`（mock PG/Redis为内存实现，但保留操作逻辑）
2. 增加P99统计采样（至少100次）
3. 增加warmup步骤消除冷启动影响
4. 分阶段测量：撤回事件发布延迟、Handler处理延迟、SG输出拦截延迟

---

### P0-3: HSM sign()方法使用PSS填充，但test_hsm_basic.py docstring声称PKCS1v15

**严重程度**：P0 — 文档与实现不一致，可能误导开发者

**发现**：
- [hsm.py:492-499](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L492-L499) `sign()` 使用 `padding.PSS`
- [hsm.py:647-651](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L647-L651) `sign_jwt()` 使用 `padding.PKCS1v15`（RS256标准要求，正确）
- [test_hsm_basic.py:15](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/sec/test_hsm_basic.py#L15) docstring写"测试HSM签名填充方式为PKCS1v15"，但实际测试仅验证sign/verify往返，**未断言填充类型**

**影响**：
1. `sign()` 使用PSS是正确的设计选择（通用签名更安全），但docstring误导
2. 04-安全合规.md仅写"RS256"，未明确区分JWT签名(PKCS1v15)和通用签名(PSS)的填充差异

**修复建议**：
1. 修正 `test_hsm_basic.py` 的docstring，准确反映sign()使用PSS
2. 在04-安全合规.md中增加填充方式说明：JWT签名→PKCS1v15(RS256标准)，通用签名→PSS(更安全)
3. `test_sec_craft_fixes.py` 已正确区分两种填充，无需修改

---

### P0-4: HSM Manager中未await的协程导致RuntimeWarning和潜在资源泄漏

**严重程度**：P0 — 每次HSM初始化/降级/密钥操作都产生RuntimeWarning，可能丢失持久化数据

**发现**：
测试运行时产生110个warnings，其中大量为：
```
RuntimeWarning: coroutine 'HSMManager._restore_keys_from_redis.<locals>.restore' was never awaited
RuntimeWarning: coroutine 'HSMManager._persist_degraded_state.<locals>.persist' was never awaited
RuntimeWarning: coroutine 'HSMManager._migrate_legacy_keys.<locals>.migrate' was never awaited
```

这些来自 [hsm.py:988](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L988)、[hsm.py:844](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L844)、[hsm.py:1036](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L1036) 的 `_safe_run_async_task()` 调用。

**影响**：
1. 在同步上下文中启动异步任务但未正确await，可能导致Redis持久化操作静默失败
2. HSM降级状态可能未正确持久化到Redis，重启后状态丢失
3. 密钥迁移可能未完成，导致旧版密钥不可用

**修复建议**：
1. 审查 `_safe_run_async_task()` 实现，确认是否正确处理了事件循环不存在的情况
2. 对于关键操作（降级状态持久化、密钥恢复），考虑使用 `asyncio.run_coroutine_threadsafe()` 替代
3. 增加操作结果验证，确认异步操作确实完成

---

## 二、P1级问题（应当修复）

### P1-1: 三层脱敏all_passed语义与文档定义不完全对齐

**发现**：
[three_layer_desensitization.py:209-218](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py#L209-L218) 中 `all_passed` 的判断逻辑为：仅当SG层 `check_result.passed=False` 或 `degraded_mode=True` 时才为False。这意味着：
- 第1/2层 modified=True（脱敏引擎正常工作）不影响 all_passed
- 第3层降级模式（degraded_mode=True）会使 all_passed=False

**与文档偏差**：04-安全合规.md §2 定义"三道防线全部通过"为all_passed，但当前实现中第1/2层脱敏修改数据（modified=True）不算"未通过"。这在语义上是正确的（脱敏修改是预期行为），但文档表述可能引起误解。

**修复建议**：在04-安全合规.md中明确all_passed的语义：第1/2层modified=True表示正常脱敏（通过），第3层passed=False或degraded=True表示安全校验失败（未通过）。

---

### P1-2: ConsentStatus.PENDING在SG侧的处理与03-接口规范不一致

**发现**：
- 03-接口规范.md §5.1 明确：无同意记录→403+E1008（PatientNotConsentedException）
- [consent_checker.py:67-71](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L67-L71) 在无记录时返回 `ConsentStatus.PENDING` 并记录warning
- [consent_phase.py:79](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker/consent_phase.py#L79) 将PENDING和DENIED同等对待→返回failed

**影响**：SG侧将"无记录"视为PENDING而非DENIED，虽然最终效果相同（都拒绝输出），但语义不同。PENDING暗示"待确认"，DENIED暗示"明确拒绝"。

**修复建议**：对齐03-接口规范，将consent_checker.py中无记录时的返回值改为DENIED（或增加PENDING→DENIED的映射逻辑），并更新日志消息。

---

### P1-3: 审计服务AuditService构造函数签名不一致

**发现**：
- [audit_service.py:49](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/audit_service.py#L49) `AuditService.__init__(self, db: AsyncSession)` 要求传入db参数
- [consent_withdrawal_consumer.py:99](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/consent_withdrawal_consumer.py#L99) `AuditService()` 无参数调用——这会运行时报错

**影响**：SafetyActionHandler和DataCleanupHandler中创建AuditService时未传入db参数，运行时会抛出TypeError。

**修复建议**：在handler中正确传入db session，或为AuditService提供无参数的工厂方法。

---

### P1-4: desensitization_restore.py速率限制仅使用内存计数器，未使用Redis

**发现**：
[desensitization_restore.py:139-150](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/desensitization_restore.py#L139-L150) 速率限制使用 `_rate_limit_counter` 内存字典，仅在进程内有效。

**影响**：多实例部署时速率限制失效——每个实例独立计数，总限制为 N × 配置值。

**修复建议**：使用Redis实现分布式速率限制（如滑动窗口或令牌桶），或至少在文档中明确单实例部署约束。

---

### P1-5: Compact密钥轮换60天差异化已实现但文档未更新

**发现**：
- [settings.py:71](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L71) `desensitization_key_rotation_days_compact: int = 60`
- [settings.py:204-212](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L204-L212) compact模式下自动覆盖为60天
- 04-安全合规.md §2.1.1 仍写"密钥轮换周期180天"，未区分compact/full

**修复建议**：更新04-安全合规.md，增加compact模式60天差异化说明。

---

### P1-6: JWT 120天自动轮换已实现但文档未更新

**发现**：
- [settings.py:79](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L79) `jwt_key_rotation_days: int = 120`
- [hsm.py:310-328](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L310-L328) `_check_jwt_key_rotation()` 定时检查并自动轮换
- 04-安全合规.md §4.1 未提及120天自动轮换机制

**修复建议**：更新04-安全合规.md §4.1，增加JWT密钥120天自动轮换说明。

---

### P1-7: SG consent_checker查询ConsentRecord但SG使用独立数据库模型

**发现**：
- [consent_checker.py:56-61](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L56-L61) 查询 `safety_gateway.sg_models.ConsentRecord`
- 主服务 [database.py:23-37](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/models/database.py#L23-L37) 定义 `app.models.database.ConsentRecord`
- 两个ConsentRecord模型是否字段完全一致需要验证

**修复建议**：确认SG和主服务的ConsentRecord模型字段一致，如有差异需记录并说明原因。

---

### P1-8: 同意撤回消费者默认仅启用safety_action handler

**发现**：
[consent_withdrawal_consumer.py:337-338](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/consent_withdrawal_consumer.py#L337-L338) 默认handler配置为 `["safety_action"]`，不包含 `data_cleanup`。

**影响**：同意撤回后脱敏映射缓存不会被自动清理，可能导致已撤回患者的脱敏映射仍可被还原。

**修复建议**：评估是否应默认启用data_cleanup handler，或在文档中明确说明需要手动配置启用。

---

### P1-9: 性能基准测试普遍缺少warmup步骤

**发现**：
- HSM签名基准测试（1000次采样）无warmup
- 脱敏基准测试（1000次采样）无warmup
- 同意撤回基准测试（100次采样）无warmup
- 唯一例外：`test_revocation_performance.py` 中 `TestHSMPerformance.test_sign_p99_latency` 有50次warmup

**影响**：首次调用包含模块加载、JIT编译等冷启动开销，可能使P99测量偏高，CI环境下不稳定。

**修复建议**：所有性能基准测试增加10-50次warmup迭代，排除冷启动影响。

---

### P1-10: PytestUnknownMarkWarning——自定义mark未注册

**发现**：
测试运行时产生多个 `PytestUnknownMarkWarning: Unknown pytest.mark.track_sec` 警告。

**修复建议**：在 `pyproject.toml` 或 `pytest.ini` 中注册自定义mark：
```ini
[tool.pytest.ini_options]
markers = ["track_sec", "track_sg", "track_inf"]
```

---

## 三、P2级问题（建议修复）

### P2-1: 04-安全合规.md缺少JWT签名填充方式说明

**发现**：文档仅写"RS256"，未区分JWT签名(PKCS1v15)和通用签名(PSS)的填充差异。RS256隐含PKCS1v15，但文档应显式说明。

**修复建议**：在04-安全合规.md §4.1增加说明：
- JWT签名使用RS256(PKCS#1 v1.5填充)，符合RFC 7518
- 通用数据签名使用RSASSA-PSS(SHA-256)，提供可证明安全性

---

### P2-2: desensitization_rules.yaml与desensitization_rules_safety.yaml异构性可进一步加强

**发现**：
- 主规则 `id_card` 使用 `(?<![*\d])\d{17}[\dXx](?![*\d])`（宽松匹配）
- SG规则 `sg_id_card` 使用 `[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]`（精确日期校验）
- 异构性已实现 ✅，但主规则的身份证正则过于宽松（不校验日期部分），可能产生更多误报

**修复建议**：考虑加强主规则的身份证正则精度，或保持当前设计（宽松→减少漏报 vs 精确→减少误报，异构冗余本身就是互补）。

---

### P2-3: SafetyGateway客户端熔断器参数硬编码

**发现**：
[safety_gateway_client.py:14](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py#L14) `FAST_FAIL_THRESHOLD = 3` 硬编码，而SG侧的熔断器参数已移至settings。

**修复建议**：将 `FAST_FAIL_THRESHOLD` 移至 `settings.py`，与SG侧保持一致的配置管理方式。

---

### P2-4: 审计链锁的LRU淘汰策略可能导致长会话锁被意外回收

**发现**：
[audit_service.py:27-28](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/audit_service.py#L27-L28) `_MAX_CHAIN_LOCKS = 10000`，当锁数量超过上限时淘汰最早的锁。

**影响**：如果活跃会话数超过10000，长会话的锁可能被回收，导致并发写入同一链时哈希链断裂。

**修复建议**：评估10000上限是否足够，或改用基于会话活跃度的淘汰策略（如淘汰最近N分钟未写入的锁）。

---

### P2-5: consent_withdrawal_consumer.py中fields解析代码冗余

**发现**：
[consent_withdrawal_consumer.py:274-288](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/consent_withdrawal_consumer.py#L274-L288) 中每个字段的解析都重复了 `fields.get(b"key", fields.get("key", b"")).decode() if isinstance(...) else str(...)` 模式。

**修复建议**：提取为辅助函数 `def _decode_field(fields, key) -> str`。

---

### P2-6: SafetyActionHandler中多次获取Redis客户端

**发现**：
[consent_withdrawal_consumer.py:39-95](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/consent_withdrawal_consumer.py#L39-L95) 中 `SafetyActionHandler.handle_withdrawal()` 三次调用 `await RedisPool.get_client()`。

**修复建议**：在方法开头获取一次Redis客户端并复用。

---

### P2-7: 数据保留策略配置已实现但缺少定时任务

**发现**：
- `settings.py` 中有 `data_retention_outpatient_years=15`、`data_retention_inpatient_years=30` 等配置
- `data_retention.py` 有 `archive_expired_sessions()` 方法
- 但未发现定时调用该方法的调度逻辑

**修复建议**：增加定时归档任务（如每日凌晨执行），或确认是否由外部调度系统触发。

---

### P2-8: 缺少患者数据导出API

**发现**：04-安全合规.md §6要求"患者数据导出规范"（《个人信息保护法》第45条合规），但当前API端点中未发现数据导出接口。

**修复建议**：评估是否在当前阶段实现，或在后续迭代中补充。

---

### P2-9: 缺少API Key生命周期管理完整实现

**发现**：04-安全合规.md §1要求API Key"初始配置/180天轮换/泄露吊销/注销"，但当前设备管理API可能未完整实现轮换和吊销逻辑。

**修复建议**：确认设备管理API的完整性，补充缺失的轮换和吊销端点。

---

### P2-10: 缺少边缘端DICOM元数据剥离实现验证

**发现**：04-安全合规.md §1要求"边缘端解码后立即剥离身份标识字段"，但当前代码库中边缘端代码可能未完整实现此功能。

**修复建议**：确认边缘端DICOM处理流程是否包含元数据剥离步骤。

---

### P2-11: http_exception_handler中503映射为E2001而非E4005

**发现**：
[errors.py:394](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/errors.py#L394) `503: "E2001"`，但503通常表示基础设施不可用（如SG/PG/HSM），应映射为E4xxx系列。

**修复建议**：评估503→E4005是否更合适，或保持当前映射但在文档中说明理由。

---

### P2-12: SafetyGateway客户端health_probe间隔硬编码

**发现**：
[safety_gateway_client.py:23](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py#L23) `_health_probe_interval = 4.0` 硬编码。

**修复建议**：移至settings.py配置。

---

### P2-13: SQLAlchemy declarative_base()使用已废弃导入方式

**发现**：
[database.py:33](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/database.py#L33) 使用 `declarative_base()` 产生 `MovedIn20Warning`。

**修复建议**：改为 `from sqlalchemy.orm import declarative_base`。

---

## 四、设计规范一致性审查结果

### 4.1 对照04-安全合规.md（24项检查）

| # | 审查项 | 状态 | 备注 |
|---|--------|------|------|
| 1 | 三层脱敏职责分层 | ✅ | 标准化层→融合层→SG，职责清晰 |
| 2 | SG规则异构冗余 | ✅ | 主规则宽松+SG精确，互补设计 |
| 3 | 双重降级场景 | ✅ | consent_phase.py处理DB+融合层同时不可用(E4005C) |
| 4 | HSM四槽位定义 | ✅ | JWT_SIGNING/DATA_ENCRYPTION/DESENSITIZATION_MAPPING/EMERGENCY_SIGNING |
| 5 | HSM降级四阶段 | ✅ | NORMAL→CACHED(5min)→RESTRICTED(30min)→EMERGENCY |
| 6 | 密钥轮换7天并行期 | ✅ | rotate_key(parallel_days=7) |
| 7 | SoftHSM 90天/120天 | ✅ | SOFT_HSM_EXPIRY_DAYS=90, SOFT_HSM_CRITICAL_DAYS=120 |
| 8 | Compact密钥轮换60天 | ✅ | settings.py已实现，**文档未更新** |
| 9 | 应急签发双人复核 | ✅ | operator_id != approver_id 强制 |
| 10 | 应急签发启用条件 | ✅ | HSM不可用>5min+管理员确认 |
| 11 | 同意状态PG权威源 | ✅ | consent_checker直查PG，无Redis缓存 |
| 12 | 两阶段同意校验 | ✅ | 入口快速拒绝+SG输出前直查 |
| 13 | 撤回后<500ms生效 | ⚠️ | 基准测试使用MockHandler，**真实延迟未验证** |
| 14 | Token Family机制 | ✅ | family_id关联、重放检测撤销整个family |
| 15 | 重放检测撤销整个family | ✅ | revoke_token_family() |
| 16 | 并发会话限制(默认5) | ✅ | max_concurrent_sessions_per_user=5 |
| 17 | 脱敏映射AES-256-GCM | ✅ | AESGCM(aes_key) with 12-byte IV |
| 18 | 密钥版本key_version | ✅ | 每条映射记录含key_version |
| 19 | 还原速率限制分档 | ⚠️ | 仅内存计数器，**多实例失效** |
| 20 | 审计链按session_id分链 | ✅ | _get_chain_lock(session_id) |
| 21 | 错误码体系 | ✅ | E1008/E1010/E4005/E4007等已定义 |
| 22 | 数据保留策略 | ✅ | 配置已实现，**缺少定时任务** |
| 23 | JWT RS256签名 | ✅ | 强制RS256，禁止HS256 |
| 24 | JWT 120天轮换周期 | ✅ | _check_jwt_key_rotation()已实现，**文档未更新** |

### 4.2 对照03-接口规范.md（8项检查）

| # | 审查项 | 状态 | 备注 |
|---|--------|------|------|
| 1 | 同意接口GET | ✅ | 403+E1008/E1010行为正确 |
| 2 | 同意接口POST | ✅ | 撤回时Redis Streams广播 |
| 3 | 诊断接口consent_status_at_output | ✅ | SG输出前直查PG |
| 4 | 错误码映射 | ⚠️ | 503→E2001而非E4xxx |
| 5 | 数据库表结构 | ✅ | 字段与文档基本一致 |
| 6 | 设备管理接口 | ⚠️ | API Key轮换/吊销可能不完整 |
| 7 | API限流 | ✅ | 诊断10/分、影像30/分、急诊5倍 |
| 8 | WebSocket认证 | 未审查 | 需单独验证 |

### 4.3 对照01-系统架构设计.md（6项检查）

| # | 审查项 | 状态 | 备注 |
|---|--------|------|------|
| 1 | SG独立进程 | ✅ | safety_gateway/独立模块 |
| 2 | SG降级策略 | ✅ | 熔断器+快速失败+降级模式 |
| 3 | SG输出前同意校验 | ✅ | consent_checker直查PG |
| 4 | 会话状态三阶段 | ✅ | 热→温→归档实现 |
| 5 | 诊断结果缓存 | 未审查 | 需确认Redis缓存键含KB版本号 |
| 6 | 边缘端API Key+HKDF | ⚠️ | 需确认派生密钥方案实现 |

### 4.4 对照00a-术语表.md（3项检查）

| # | 审查项 | 状态 | 备注 |
|---|--------|------|------|
| 1 | 错误码命名 | ✅ | 与术语表一致 |
| 2 | 状态枚举 | ⚠️ | ConsentStatus缺PENDING |
| 3 | 配置项命名 | ✅ | 与术语表基本一致 |

### 4.5 对照00b-统一配置参考手册.md（4项检查）

| # | 审查项 | 状态 | 备注 |
|---|--------|------|------|
| 1 | 配置项默认值 | ✅ | 与手册基本一致 |
| 2 | compact/full差异 | ✅ | 密钥轮换/引擎/GPU等差异化已实现 |
| 3 | 配置约束关系 | ✅ | 端口冲突检测、阈值校验已实现 |
| 4 | SG配置项 | ✅ | sg_db_retry/sg_circuit_breaker等已配置 |

---

## 五、代码质量审查结果

### 5.1 并发安全

| 组件 | 机制 | 评估 |
|------|------|------|
| HSM密钥操作 | `threading.RLock()` + 单例 | ✅ 线程安全 |
| 脱敏引擎 | `threading.Lock()` 快照模式 | ✅ 线程安全 |
| 审计链写入 | `asyncio.Lock()` per-session | ✅ 异步安全 |
| Token存储 | `threading.RLock()` | ✅ 线程安全 |
| 脱敏还原 | `threading.Lock()` | ⚠️ 仅进程内，多实例不安全 |
| SG客户端 | `asyncio.Lock()` | ✅ 异步安全 |

### 5.2 安全漏洞检查

| 检查项 | 状态 | 备注 |
|--------|------|------|
| 私钥导出禁止 | ✅ | get_private_key()直接抛异常 |
| 硬编码密钥 | ✅ | 生产环境强制HSM_PRIVATE_KEY_PASSWORD和HSM_KEY_ENCRYPTION_KEY |
| API Key哈希存储 | ✅ | SHA-256+salt |
| 日志泄漏 | ✅ | Token/密钥仅显示前几位 |
| 时序攻击 | ⚠️ | 未发现constant_time_compare使用，但JWT库内部处理 |
| SQL注入 | ✅ | 使用ORM参数化查询 |
| SSRF | ✅ | SG客户端仅连接配置的host:port |

### 5.3 降级策略完整性

| 降级场景 | 实现 | 评估 |
|----------|------|------|
| HSM四阶段降级 | ✅ | NORMAL→CACHED→RESTRICTED→EMERGENCY |
| SG降级 | ✅ | 熔断器+快速失败+降级模式 |
| 双重降级(SG+融合层) | ✅ | E4005C错误码 |
| Redis降级 | ⚠️ | 部分功能有降级，需确认完整性 |
| PG降级 | ✅ | consent_checker返回error→E4005B |

---

## 六、测试逻辑有效性审查结果

### 6.1 测试统计

| 指标 | 当前值 | 目标 | 状态 |
|------|--------|------|------|
| SEC测试总数 | 1038 | ≥1000 | ✅ |
| 全部通过 | 1038 passed | 0 failed | ✅ |
| Warnings | 110 | 0 | ⚠️ |

### 6.2 测试覆盖薄弱环节

| 领域 | 覆盖情况 | 风险 |
|------|----------|------|
| 同意撤回<500ms真实延迟 | 仅MockHandler基准+单次真实调用 | 高 |
| 多实例并发安全 | 无分布式锁测试 | 中 |
| HSM密钥Redis持久化恢复 | 有测试但RuntimeWarning | 中 |
| SG独立服务端到端 | 有单元测试，缺集成测试 | 中 |
| 边界条件（空值/None/超长） | 部分覆盖 | 低 |
| 性能基准warmup | 几乎全部缺失 | 低 |

### 6.3 断言有效性评估

| 测试文件 | 断言质量 | 备注 |
|----------|----------|------|
| test_sec_craft_fixes.py | ✅ 高 | 明确区分PSS/PKCS1v15，精确断言 |
| test_hsm_basic.py | ⚠️ 中 | docstring与实现不一致 |
| test_revocation_latency_benchmark.py | ⚠️ 低 | MockHandler不反映真实延迟 |
| test_sec_p0_critical.py | ✅ 高 | 覆盖关键安全路径 |
| test_desensitization.py | ✅ 高 | 参数化PII组合覆盖全面 |

---

## 七、改进方案优先级排序

### 立即修复（P0）

| # | 问题 | 工作量 | 修复方案 |
|---|------|--------|----------|
| P0-1 | ConsentStatus枚举不一致 | 小 | schemas.py增加PENDING或consent_checker改返回DENIED |
| P0-2 | 撤回SLA基准测试无效 | 中 | 引入真实Handler+P99统计+warmup |
| P0-3 | test_hsm_basic.py docstring错误 | 小 | 修正docstring |
| P0-4 | HSM未await协程 | 中 | 审查_safe_run_async_task实现 |

### 尽快修复（P1）

| # | 问题 | 工作量 |
|---|------|--------|
| P1-1 | all_passed语义文档对齐 | 小 |
| P1-2 | PENDING vs DENIED语义对齐 | 小 |
| P1-3 | AuditService构造函数不一致 | 小 |
| P1-4 | 速率限制多实例失效 | 中 |
| P1-5 | Compact轮换文档更新 | 小 |
| P1-6 | JWT轮换文档更新 | 小 |
| P1-7 | SG/主服务ConsentRecord一致性验证 | 小 |
| P1-8 | 默认handler配置评估 | 小 |
| P1-9 | 性能基准warmup | 小 |
| P1-10 | pytest mark注册 | 小 |

### 建议修复（P2）

| # | 问题 | 工作量 |
|---|------|--------|
| P2-1~P2-13 | 文档更新/代码优化 | 小~中 |

---

## 八、文档更新建议

| 文档 | 章节 | 更新内容 |
|------|------|----------|
| 04-安全合规.md | §2.1.1 | 增加compact模式60天密钥轮换差异化说明 |
| 04-安全合规.md | §4.1 | 增加JWT密钥120天自动轮换说明 |
| 04-安全合规.md | §4.1 | 增加签名填充方式说明（JWT→PKCS1v15，通用→PSS） |
| 04-安全合规.md | §2 | 明确all_passed语义定义 |
| 03-接口规范.md | §4 | 评估503错误码映射是否应为E4xxx |
| 00a-术语表.md | §十三 | 增加ConsentStatus.PENDING定义 |

---

## 九、自反思记录

### 审查方法论反思

1. **文档先行**：先读6份核心参考文档再审查代码，确保审查有据可依，这个策略有效
2. **测试驱动**：先运行测试确认当前状态（1038 passed），再深入审查测试逻辑有效性，避免了"测试通过=功能正确"的误区
3. **异构验证**：通过多个搜索agent并行审查不同维度，提高了审查效率
4. **不足**：WebSocket认证、边缘端代码、DICOM处理流程未深入审查，需后续补充

### 关键教训

1. **Mock测试的陷阱**：同意撤回<500ms SLA的基准测试使用MockHandler，测试通过但无法验证真实SLA——这提醒我们，性能基准测试必须使用与生产环境等价的实现
2. **枚举一致性的重要性**：两个独立定义的ConsentStatus枚举看似无害，但PENDING的缺失可能导致跨模块比较失败——分布式系统中的类型定义必须统一
3. **文档与实现的同步**：多个功能已实现但文档未更新（compact轮换、JWT自动轮换、填充方式），这会导致后续开发者误解系统行为
