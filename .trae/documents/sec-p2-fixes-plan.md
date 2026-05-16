# SEC 安全合规轨道 P2 中等优先级问题修复计划

## 修复总览

共 25 项 P2 问题（P2-19 已在 P0-4 中修复，实际剩余 24 项），按模块分组实施。

---

## 阶段一：HSM 模块修复（P2-1, P2-2, P2-3）

### 步骤 1.1：P2-1 — sign_jwt() algorithm 参数一致性校验

**文件**: `app/security/hsm.py`
**问题**: `sign_jwt()` 接受 `algorithm` 参数但内部始终硬编码 PKCS1v15+SHA256，传入 RS384/RS512 时 header 与签名不匹配
**修复方案**:
- 在 `sign_jwt()` 方法开头添加 algorithm 参数校验：仅允许 "RS256"
- 如果传入非 RS256，记录 warning 日志并强制使用 RS256
- 不实现 RS384/RS512 支持（设计文档明确要求 RS256），避免增加攻击面

**验证**: 传入 `algorithm="RS384"` 时日志记录 warning 且签名仍用 RS256

### 步骤 1.2：P2-2 — _safe_run_async_task() 无事件循环时数据丢失

**文件**: `app/security/hsm.py`
**问题**: 无事件循环时 `coro.close()` 直接丢弃协程，降级状态/密钥持久化数据丢失
**修复方案**:
- 将 `coro.close()` 替换为：将待持久化数据写入本地临时文件作为降级方案
- 在 `_safe_run_async_task` 失败时，将关键数据（降级状态、密钥信息）序列化写入 `_pending_persistence` 队列
- 在 HSMManager 初始化时检查并恢复 `_pending_persistence` 队列中的数据

**验证**: 模拟无事件循环场景，验证降级状态不丢失

### 步骤 1.3：P2-3 — SoftHSM 过期检查 .days 截断

**文件**: `app/security/hsm.py`
**问题**: `(now - self._soft_hsm_enabled_at).days` 截断不足一天的部分，89天23小时不触发告警
**修复方案**:
- 将 `is_expired = days_running > self.SOFT_HSM_EXPIRY_DAYS` 改为 `is_expired = (now - self._soft_hsm_enabled_at) > timedelta(days=self.SOFT_HSM_EXPIRY_DAYS)`
- 将 `_check_soft_hsm_expiry()` 中的 `.days` 比较改为 `timedelta` 直接比较
- `days_remaining` 使用 `timedelta` 计算后取整（向上取整）

**验证**: SoftHSM 运行 89天23小时时 `is_expired` 为 False（正确），90天0小时时为 True

---

## 阶段二：应急签发模块修复（P2-4, P2-5, P2-6, P2-7）

### 步骤 2.1：P2-4 — 应急签发启用增加恢复确认参数

**文件**: `app/security/emergency_signing.py`
**问题**: 规格要求"管理员确认无法在30分钟内恢复"，但代码未实现此流程步骤
**修复方案**:
- 在 `enable_emergency_signing()` 中添加 `recovery_confirmed: bool = False` 参数
- 当 `recovery_confirmed=False` 时，记录 warning 日志但仍然允许启用（流程层面由 UI 强制确认）
- 添加审计日志记录 `recovery_confirmed` 状态

**验证**: `recovery_confirmed=False` 时审计日志标记"未确认恢复可能性"

### 步骤 2.2：P2-5 — 应急密钥自动生成添加审计日志

**文件**: `app/security/emergency_signing.py`
**问题**: `enable_emergency_signing()`、`sign_emergency()`、`sign_jwt_emergency()` 中密钥自动生成无审计日志
**修复方案**:
- 在三处 `hsm_manager.generate_key_pair()` 调用后，添加审计日志记录
- 审计事件：`emergency_key_auto_generated`，级别 CRITICAL
- 记录：key_name、slot、调用来源（enable/sign/sign_jwt）

**验证**: 密钥自动生成后审计日志中出现 CRITICAL 级别记录

### 步骤 2.3：P2-6 — verify_emergency_signature() 返回类型修复

**文件**: `app/security/emergency_signing.py`
**问题**: 异常分支返回 None 而非 False，与 `-> bool` 不一致
**修复方案**:
- 将外层 `except Exception` 块中的 `return None` 改为 `return False`
- 添加 `logger.critical()` 记录验证过程异常（区分"签名不匹配"和"验证过程出错"）

**验证**: 异常场景返回 False 而非 None

### 步骤 2.4：P2-7 — 应急签发状态 TTL 改为从 HSM 恢复时计时

**文件**: `app/security/emergency_signing.py`
**问题**: TTL 从启用时开始计时，但规格要求"HSM恢复后24小时内完成补签名验证"
**修复方案**:
- 在 `disable_emergency_signing()` 中（HSM 恢复时调用），重新设置 Redis key 的 TTL 为 `verification_hours * 3600`
- 这样验证窗口从 HSM 恢复时开始，而非从启用时开始
- 在 `enable_emergency_signing()` 中设置较长的初始 TTL（如 72 小时），作为安全上限

**验证**: HSM 恢复后禁用应急签发时，验证窗口 TTL 被重置

---

## 阶段三：脱敏还原模块修复（P2-8, P2-9, P2-10）

### 步骤 3.1：P2-8 — re_encrypt_with_new_key 事务性保证

**文件**: `app/security/desensitization_restore.py`
**问题**: 批量重加密中途失败，已提交数据用新密钥、未提交用旧密钥，状态不一致
**修复方案**:
- 在每批处理前，先记录当前批次的最大 mapping.id 到变量 `last_processed_id`
- 每批 commit 后更新 `last_processed_id`
- 重加密入口添加 `resume_from_id` 参数，支持从中断点恢复
- 失败时抛出 `ReEncryptionError` 异常（含已处理数量和 resume_from_id），调用方可据此恢复

**验证**: 模拟中途失败，验证可从断点恢复

### 步骤 3.2：P2-9 — 内存级速率限制改为 asyncio.Lock

**文件**: `app/security/desensitization_restore.py`
**问题**: `_check_rate_limit()` 使用 `threading.Lock`，在 async 环境中不正确；多实例部署时内存限速无效
**修复方案**:
- 将 `_check_rate_limit()` 改为 `async` 方法，使用 `asyncio.Lock`
- 内存限速仅作为快速拒绝的第一道防线（减少 Redis 调用），Redis 限速为权威
- 内存计数通过后 Redis 限速拒绝时，回滚内存计数

**验证**: 并发还原请求正确限速

### 步骤 3.3：P2-10 — batch_restore 性能优化

**文件**: `app/security/desensitization_restore.py`
**问题**: 串行逐条恢复，每条独立走限速检查，批量操作耗尽配额
**修复方案**:
- 修改返回类型为 `Dict[str, Optional[str]]`
- 批量操作仅做一次限速检查（检查 `len(record_ids)` 是否超过剩余配额）
- 使用 `asyncio.gather` 并发恢复（限制并发数为 5）
- 批量查询数据库获取所有 mapping 记录，减少 DB 往返

**验证**: 100 条批量恢复性能显著提升

---

## 阶段四：三层脱敏模块修复（P2-11, P2-12, P2-13, P2-14）

### 步骤 4.1：P2-11 — pii_leakage_detected 语义修正

**文件**: `app/security/three_layer_desensitization.py`
**问题**: `pii_leakage_detected` 实际含义是"安全校验未通过或降级"，降级≠泄露
**修复方案**:
- 将 `pii_leakage_detected` 重命名为 `security_check_failed`
- 添加新字段 `degraded_mode: bool` 区分"校验失败"和"降级运行"
- `security_check_failed = not all_passed`（任一层不通过）
- `degraded_mode = any(layer.degraded for layer in results)`（任一层降级）

**验证**: 降级模式下 `security_check_failed=True, degraded_mode=True`；校验失败时 `security_check_failed=True, degraded_mode=False`

### 步骤 4.2：P2-12 — 统一 DesensitizationRule 类型

**文件**: `app/common/desensitization/rule.py`, `app/security/desensitization.py`, `safety_gateway/desensitization_rules.py`
**问题**: 三处独立定义 `DesensitizationRule`，类型不兼容
**修复方案**:
- 以 `app/common/desensitization/rule.py` 的 `DesensitizationRule` 为基础，添加 `is_patch: bool = False` 字段
- 修改 `app/security/desensitization.py` 中的 `DesensitizationRule` 为从 common 导入
- 修改 `safety_gateway/desensitization_rules.py` 中的 `DesensitizationRule` 为从 common 导入
- 删除各模块中的独立 `DesensitizationRule` 定义

**验证**: 三处使用同一 `DesensitizationRule` 类型

### 步骤 4.3：P2-13 — 标记死代码 base.py 和 recursive.py

**文件**: `app/common/desensitization/base.py`, `app/common/desensitization/recursive.py`
**问题**: 两个文件为死代码，未被任何引擎继承
**修复方案**:
- 不删除（按 AGENTS.md 指导，不删除预存在的死代码）
- 在文件顶部添加 `# DEPRECATED: This module is not used by any engine. See individual engine implementations.` 标记
- 更新 `__init__.py` 中的导出，标记为 deprecated

**验证**: 标记清晰，不影响现有功能

### 步骤 4.4：P2-14 — 融合层引擎独立性注释

**文件**: `app/fusion/desensitization.py`
**问题**: 融合层直接复用标准化层引擎，非独立引擎
**修复方案**:
- 当前设计是有意为之（融合层 = 标准化层 + 额外模式），不需要改为完全独立引擎
- 添加详细注释说明设计决策：融合层复用标准化层是"纵深防御"策略，第一遍用标准化规则捕获已知模式，第二遍用融合层特有模式捕获 LLM 生成场景
- 不做代码变更，仅补充文档注释

**验证**: 注释清晰说明设计意图

---

## 阶段五：同意模块修复（P2-15, P2-16, P2-17, P2-18, P2-20）

### 步骤 5.1：P2-15 — ConsentStatus 枚举统一

**文件**: `safety_gateway/validation_result.py`, `app/models/schemas.py`
**问题**: `ConsentStatus` 在两处独立定义，未来可能不一致
**修复方案**:
- SG 作为独立进程无法导入主应用模块，保持两处独立定义
- 在两处定义上方添加一致性约束注释：`# CONSISTENCY: Must match app.models.schemas.ConsentStatus exactly`
- 添加跨模块一致性测试：在 `tests/unit/sec/test_config_doc_consistency.py` 中添加测试验证两处枚举值一致

**验证**: 一致性测试通过

### 步骤 5.2：P2-16 — data_retention 门诊/住院归档区分

**文件**: `app/services/data_retention.py`
**问题**: `purge_expired_archives()` 无法按患者类型区分门诊/住院
**修复方案**:
- 在 `SessionArchive` 模型中添加 `patient_type` 字段（如果不存在）
- 修改 `purge_expired_archives()` 查询条件，按 `patient_type` 过滤
- 修改 `verify_retention_compliance()` 分别查询门诊/住院归档

**验证**: 门诊归档 15 年后清除，住院归档 30 年后清除

### 步骤 5.3：P2-17 — data_retention 闰年修正

**文件**: `app/services/data_retention.py`
**问题**: `timedelta(days=retention_years * 365)` 未考虑闰年
**修复方案**:
- 将 `timedelta(days=retention_years * 365)` 改为 `timedelta(days=int(retention_years * 365.25))`
- 不引入 `dateutil.relativedelta` 依赖（保持简单）
- 30 年差异：365.25*30=10957.5 天 vs 365*30=10950 天，差 7.5 天，对合规性更安全

**验证**: 30 年保留期计算结果为 10957 天

### 步骤 5.4：P2-18 — consent_repository 访问私有属性

**文件**: `app/repositories/consent_repository.py`
**问题**: 直接访问 `hsm_manager._degraded_at` 私有属性
**修复方案**:
- 将 `hsm_manager._degraded_at` 替换为 `hsm_manager.get_degraded_at()` 公共方法
- 如果 `get_degraded_at()` 不存在，在 `HSMManager` 中添加该方法

**验证**: 不再访问任何 `_` 前缀属性

### 步骤 5.5：P2-20 — scan_iter 替换为分页 scan

**文件**: `app/security/consent_withdrawal_consumer.py`
**问题**: `scan_iter` 可能阻塞事件循环
**修复方案**:
- 将 `async for key in redis_client.scan_iter(pattern)` 替换为手动分页 `scan` 调用
- 每次 SCAN 设置 `count=100`，控制单次返回量
- 在循环中添加 `await asyncio.sleep(0)` 让出事件循环控制权

**验证**: 大量 key 场景下不阻塞事件循环

---

## 阶段六：SafetyGateway 模块修复（P2-21, P2-22, P2-23, P2-24, P2-25）

### 步骤 6.1：P2-21 — P99 < 5ms 硬约束执行

**文件**: `safety_gateway/main.py`, `safety_gateway/health/latency_tracker.py`
**问题**: P99 < 5ms 仅软约束（告警+限流标志），不拒绝请求
**修复方案**:
- 在 `perform_safety_check()` 入口添加限流检查：如果 `latency_tracker.is_throttling` 为 True，返回 HTTP 503 + E4005
- 在 `LatencyTracker` 中添加 `is_throttling` 属性（已有 `_throttling` 字段，添加公开属性）
- 限流状态下拒绝新请求，直到 P99 恢复到 warning 阈值以下

**验证**: P99 连续 3 次超过 5ms 后新请求被拒绝

### 步骤 6.2：P2-22 — CheckPhase 基类 check() 协议修复

**文件**: `safety_gateway/checker/phase_base.py`
**问题**: 基类 `check(context)` 协议被所有子类违反，子类使用 `check_with_params()`
**修复方案**:
- 在 `CheckPhase` 基类中，将 `check(context)` 改为从 context 中提取参数并委托给 `check_with_params()`
- 添加 `check_with_params()` 抽象方法（子类实现）
- `check()` 成为模板方法，从 `CheckContext` 提取参数调用 `check_with_params()`
- 各子类的 `check()` 不再抛 NotImplementedError

**验证**: 编排器可通过统一 `check(context)` 接口调用所有阶段

### 步骤 6.3：P2-23 — ConsistencyPhase EVIDENCE_INSUFFICIENT 死代码清理

**文件**: `safety_gateway/checker/consistency_phase.py`
**问题**: `EVIDENCE_INSUFFICIENT` 分支在 `check_with_params()` 中可达但语义与 `CONTRADICTORY`/`PARTIAL_CONSISTENT` 不一致
**修复方案**:
- 将 `EVIDENCE_INSUFFICIENT` 的处理从 `CheckResult.failed()` 改为 `CheckResult.degraded()`
- 依据不足应降级输出（要求人工复核），而非直接拒绝
- 与 `PARTIAL_CONSISTENT` 处理策略保持一致

**验证**: 依据不足时返回降级结果而非拒绝

### 步骤 6.4：P2-24 — main.py 降级输出 fusion_available 修复

**文件**: `safety_gateway/main.py`
**问题**: 降级输出时 `fusion_available=False` 硬编码，应使用实际融合层状态
**修复方案**:
- 将 `fusion_available=False` 改为从 `check_result` 或 `health_checker` 获取实际融合层可用性
- 使用 `check_result` 中已有的融合层状态（`perform_full_check` 已查询并写入 context）
- 如果 `check_result` 中无融合层状态，再从 `health_checker.get_fusion_availability()` 查询

**验证**: 融合层可用时降级输出包含更多字段

### 步骤 6.5：P2-25 — LatencyTracker 状态修改加锁

**文件**: `safety_gateway/health/latency_tracker.py`
**问题**: `_critical_count`/`_throttling` 修改在锁外
**修复方案**:
- 将 `_critical_count` 和 `_throttling` 的修改移入 `async with self._lock` 块内
- `compute_stats()` 中所有状态修改都在锁保护下执行

**验证**: 并发调用 `compute_stats()` 不产生竞态条件

---

## 实施顺序与验证策略

| 批次 | 步骤 | 涉及文件数 | 验证方式 |
|------|------|-----------|---------|
| 1 | 阶段一（HSM: P2-1,2,3） | 1 | `pytest tests/unit/sec/test_hsm_basic.py tests/unit/sec/test_hsm_key_slot.py` |
| 2 | 阶段二（应急签发: P2-4,5,6,7） | 1 | `pytest tests/unit/sec/test_emergency_signing.py` |
| 3 | 阶段三（脱敏还原: P2-8,9,10） | 1 | `pytest tests/unit/sec/test_desensitization_restore.py` |
| 4 | 阶段四（三层脱敏: P2-11,12,13,14） | 5 | `pytest tests/unit/sec/test_desensitization.py` |
| 5 | 阶段五（同意: P2-15,16,17,18,20） | 4 | `pytest tests/unit/sec/test_consent_withdrawal_consumer.py tests/unit/sec/test_security_compliance.py` |
| 6 | 阶段六（SafetyGateway: P2-21,22,23,24,25） | 5 | `pytest tests/unit/sg/` |

最终验证: `pytest tests/unit/sec/ tests/unit/sg/ -q`
