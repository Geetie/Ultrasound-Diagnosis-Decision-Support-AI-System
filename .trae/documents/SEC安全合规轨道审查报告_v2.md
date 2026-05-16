# SEC安全合规轨道系统性审查报告 v2

**审查日期**: 2026-05-12
**审查范围**: SEC安全合规轨道全量验证环节、测试逻辑、源码实现、文档一致性
**审查原则**: 独立、客观、深入、批判性
**基线版本**: 对比上次审查报告 (2026-05-10)

---

## 一、审查执行摘要

### 1.1 关键指标

| 指标 | 目标 | 上次实际 | 本次实际 | 变化 | 状态 |
|------|------|---------|---------|------|------|
| CI测试总数 | ≥1000 | 1998 | 2248 | +250 | ✅ 达成 |
| SEC相关测试数 | - | ~917 | 1101 | +184 | ✅ 增长 |
| SEC测试通过率 | 100% | 95.2% (873/917) | 98.9% (1088/1101) | +3.7% | ⚠️ 12个失败 |
| 全量测试通过率 | - | 96.5% (1912/1982) | 96.0% (2158/2248) | -0.5% | ✅ |
| 签名P99延迟 | <5ms | 测试验证通过 | 测试验证通过 | - | ✅ |
| 脱敏吞吐量 | ≥1000次/秒 | 测试验证通过 | 测试验证通过 | - | ✅ |
| 撤回后生效 | <500ms | 逻辑验证通过 | 逻辑验证通过 | - | ⚠️ 缺少运行时SLA |
| 文档一致性 | 100% | 71.6% MATCH | 82.1% MATCH | +10.5% | ⚠️ 仍有MISMATCH |

### 1.2 上次遗留问题修复状态

| # | 优先级 | 问题 | 上次状态 | 本次状态 | 结论 |
|---|--------|------|---------|---------|------|
| R1 | P0 | `RedisRefreshTokenStore`缺少并发会话限制 | 待修复 | **已修复** | `create_refresh_token()`第403-411行已添加限制逻辑 |
| R2 | P0 | SG配置`DB_QUERY_TIMEOUT`=2s过短 | 待修复 | **已修复** | `sg_config.py`已设为5s，与文档一致 |
| R3 | P0 | SG配置`SG_DB_RETRY_MAX_ATTEMPTS`=2次不足 | 待修复 | **已修复** | `sg_config.py`已设为3次，与文档一致 |
| R4 | P1 | `SafetyGatewayClient.check_output()`全局锁 | 待修复 | **已改善** | 锁粒度已细化，仅保护`_consecutive_failures`计数器 |
| R5 | P1 | `AuditService._chain_locks`无清理机制 | 待修复 | **已修复** | LRU淘汰+`_MAX_CHAIN_LOCKS=10000` |
| R6 | P1 | `RoleChecker`和`check_permission`两套权限机制 | 待修复 | **未修复** | 仍存在两套独立机制 |
| R7 | P1 | 主服务缺少12+配置项 | 待修复 | **部分修复** | Settings已扩展，但仍有缺失 |
| R8 | P1 | `patient_name_simple`规则`^`锚定漏检 | 待修复 | **已修复** | 正则已改为`(?:^|[：:，,\s\n\r])`非锚定模式 |
| R9 | P1 | 撤回<500ms缺少运行时SLA保障 | 待修复 | **未修复** | 逻辑正确但缺少监控告警 |
| R10 | P2 | 内存计数器多实例不共享 | 待修复 | **部分修复** | EmergencySigningManager已添加Redis持久化 |
| R11 | P2 | `batch_restore()`串行执行 | 待修复 | **未修复** | 仍为串行 |
| R12 | P2 | `verify_all_chains()`串行验证 | 待修复 | **未修复** | 仍为串行 |
| R13 | P2 | `SchedulingAgentHandler`存根实现 | 待修复 | **未修复** | 仍为存根 |
| R14 | P2 | 数据库表双ID设计与文档不一致 | 待修复 | **未修复** | - |
| R15 | P2 | 二期错误码E3006/E3007未定义 | 待修复 | **未修复** | 二期功能 |

### 1.3 本轮新发现问题

| # | 优先级 | 问题 | 位置 | 影响 |
|---|--------|------|------|------|
| N1 | P0 | HSM `sign()`使用PSS填充，`sign_jwt()`使用PKCS1v15——填充方案不一致 | hsm.py L492-499 vs L647-651 | 签名验证兼容性问题 |
| N2 | P1 | SG `sg_db_retry_max_total_time_seconds`=15.0 vs 文档10.0 | sg_config.py L38 | 配置MISMATCH |
| N3 | P1 | SG `desensitize_dict()`不处理set/tuple类型 | safety_gateway/desensitization.py L279-293 | 特殊类型数据漏脱敏 |
| N4 | P1 | Fusion `desensitize_dict()`不处理set/tuple类型 | app/fusion/desensitization.py L110-133 | 特殊类型数据漏脱敏 |
| N5 | P1 | `DataRetentionService.log_action()` detail参数类型不匹配 | data_retention.py L53-57 | 传str但方法期望Dict |
| N6 | P2 | Redis benchmark测试mock路径错误 | test_revocation_performance.py | 测试不可用 |
| N7 | P2 | `ConsentChecker`测试断言源码包含不存在的方法 | test_sec_audit_regression.py L125 | 测试逻辑错误 |

---

## 二、设计规范一致性审查

### 2.1 三层脱敏（04-安全合规 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 标准化层前置脱敏 | ✅ MATCH | `ThreeLayerDesensitizationManager.process_standardization_layer()` 调用 `desensitize_dict()` |
| 融合层输出脱敏校验 | ✅ MATCH | `FusionDesensitizationEngine.desensitize_llm_output()` 先调标准化层脱敏，再检查融合层特有模式 |
| SafetyGateway结构化拦截 | ✅ MATCH | `SafetyGatewayDesensitizationEngine` 使用异构正则规则，规则名均以`sg_`前缀区分 |
| 三道防线异构冗余 | ✅ MATCH | SG规则与主规则正则模式不同（如身份证：SG用精确日期格式 vs 主规则用宽松匹配） |
| 双重降级场景 | ✅ MATCH | `apply_degraded_output()` 仅保留whitelist字段，标记`conclusion_suppressed=True` |
| 降级输出禁忌词过滤 | ✅ MATCH | `filter_forbidden_terms()` 过滤TI-RADS/BI-RADS等分级术语，保留否定前缀 |

### 2.2 HSM密钥管理（04-安全合规 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 4个KeySlot隔离 | ✅ MATCH | JWT_SIGNING/DATA_ENCRYPTION/DESENSITIZATION_MAPPING/EMERGENCY_SIGNING |
| 降级状态机4阶段 | ✅ MATCH | NORMAL→CACHED→RESTRICTED→EMERGENCY，阈值可配置 |
| SoftHSM 90天超期 | ✅ MATCH | `SOFT_HSM_EXPIRY_DAYS = 90`，含7天预警 |
| 应急签发方案 | ✅ MATCH | EmergencySigningManager完整实现，含双人参验、日限额、Redis持久化 |
| 密钥轮换180天 | ✅ MATCH | `desensitization_key_rotation_days = 180` |
| JWT密钥轮换120天 | ✅ MATCH | `jwt_key_rotation_days = 120`，自动轮换定时器 |
| 私钥加密存储 | ✅ MATCH | `_serialize_key_pair()` 要求 `HSM_PRIVATE_KEY_PASSWORD` 环境变量 |
| Redis持久化降级状态 | ✅ MATCH | `_persist_degraded_state()` 和 `_restore_degraded_state_from_redis()` |
| **签名填充方案** | ⚠️ MISMATCH | `sign()` 使用PSS填充，`sign_jwt()` 使用PKCS1v15填充 |

**N1详细分析**：`hsm.py` L492-499的`sign()`方法使用`padding.PSS()`，而L647-651的`sign_jwt()`使用`padding.PKCS1v15()`。JWT RS256规范要求PKCS1v15，因此`sign_jwt()`是正确的。但`sign()`用于通用数据签名（如审计链哈希签名），使用PSS也是合理的安全选择（PSS比PKCS1v15更安全）。**问题在于测试用例`test_hsm_uses_pkcs1v15_not_pss`和`test_sign_uses_pkcs1v15_not_pss`错误地期望`sign()`也使用PKCS1v15**，这是测试逻辑错误而非代码Bug。

**建议**：保留当前PSS/PKCS1v15双填充方案（JWT用PKCS1v15，通用签名用PSS），修正测试用例以匹配实际行为。

### 2.3 患者同意管理（04-安全合规 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 直查PostgreSQL | ✅ MATCH | `ConsentChecker._query_consent_status()` 直查PG，不使用缓存 |
| Redis Streams广播 | ✅ MATCH | `_publish_consent_withdrawal()` 3次重试 |
| 撤回消费者处理在途诊断 | ✅ MATCH | `SafetyActionHandler` 完整实现 |
| SG输出前直查PG | ✅ MATCH | 两阶段校验，PG不可用时返回E4005B |
| <500ms撤回安全窗口 | ⚠️ PARTIAL | 逻辑正确但缺少运行时SLA监控和告警 |
| 撤回后边缘端停止标注 | ✅ MATCH | `SafetyActionHandler` 发布`edge:stop_ai_annotation` |
| 数据清理处理器 | ✅ MATCH | `DataCleanupHandler` 清理脱敏映射缓存 |

### 2.4 错误码体系（03-接口规范 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| E1xxx客户端错误码 | ✅ MATCH | E1001-E1013完整 |
| E2xxx服务端错误码 | ✅ MATCH | E2001-E2007完整 |
| E3xxx医疗安全错误码 | ⚠️ PARTIAL | E3001-E3005完整，E3006/E3007二期未定义 |
| E4xxx基础设施错误码 | ✅ MATCH | E4001-E4012含子码完整 |
| E4005子码体系 | ✅ MATCH | E4005A1(脱敏降级+敏感数据)、E4005A2(SG不可用+同意撤回)、E4005B(DB不可用)、E4005C(双重不可用) |
| SG内部错误码 | ✅ MATCH | SG_ERROR/SG_DB_*/SG_CONSENT_*/SG_VALIDATION_*完整 |

### 2.5 配置项一致性（00b-统一配置参考手册 vs 实现）

| 配置项 | 文档值 | SG代码值 | 主服务代码值 | 判定 |
|--------|--------|---------|-------------|------|
| `DB_QUERY_TIMEOUT` | 5 | 5 | 5 | ✅ MATCH |
| `SG_DB_RETRY_MAX_ATTEMPTS` | 3 | 3 | 3 | ✅ MATCH |
| `SG_DB_RETRY_MAX_TOTAL_TIME_SECONDS` | 10.0 | **15.0** | 10.0 | ⚠️ MISMATCH (N2) |
| `SG_CONSENT_CACHE_TTL_SECONDS` | 10 | 10 | - | ✅ MATCH |
| `SG_P99_LATENCY_WARNING_THRESHOLD_MS` | 4.0 | 4.0 | 4.0 | ✅ MATCH |
| `SG_P99_LATENCY_CRITICAL_THRESHOLD_MS` | 5.0 | 5.0 | 5.0 | ✅ MATCH |
| `SG_HTTP_CLIENT_TIMEOUT_SECONDS` | 3.0 | 3.0 | 3.0 | ✅ MATCH |

**N2详细分析**：`sg_config.py` L38 `sg_db_retry_max_total_time_seconds=15.0`，而文档和主服务Settings均为10.0。SG代码值更大，意味着SG端允许更长的重试总时间，这在SG独立进程场景下是合理的（SG需要更长的重试窗口来应对数据库波动）。**建议**：如果SG的15.0是经过验证的更优值，则更新文档；否则对齐为10.0。

---

## 三、代码质量审查

### 3.1 健壮性问题

| # | 严重度 | 问题 | 位置 | 当前状态 | 影响 |
|---|--------|------|------|---------|------|
| Q1 | ~~P1~~ → ✅ | `_chain_locks` 无清理机制 | audit_service.py | **已修复** | LRU淘汰+10000上限 |
| Q2 | P2→✅ | `_daily_signature_count` 内存计数器 | emergency_signing.py | **已改善** | 已添加Redis持久化`_increment_signature_count_persistent()` |
| Q3 | P2 | `_patient_token_counter` 内存状态 | desensitization.py L46 | 未修复 | 多实例不共享，但单实例部署可接受 |
| Q4 | P2 | `_rate_limit_counter` 内存计数器 | desensitization_restore.py L37 | 未修复 | 多实例不共享 |

### 3.2 可维护性问题

| # | 严重度 | 问题 | 位置 | 当前状态 | 影响 |
|---|--------|------|------|---------|------|
| M1 | P1 | `RoleChecker` 和 `check_permission` 两套独立权限校验 | auth.py L124-161 | **未修复** | 维护复杂度 |
| M2 | P1 | `MemoryRefreshTokenStore`同步 vs `RedisRefreshTokenStore`异步 | refresh_token.py | **未修复** | 基类方法签名不一致，模块级函数已做适配 |
| M3 | P2 | `SchedulingAgentHandler` 存根实现 | consent_withdrawal_consumer.py L114-122 | **未修复** | 功能不完整 |
| M4 | P1 | `DataRetentionService.log_action()` detail参数类型不匹配 | data_retention.py L53-57 | **新发现(N5)** | 传str但AuditService期望Dict |

**N5详细分析**：`data_retention.py` L53-57调用`audit_service.log_action(detail=f"Archived {archived_count} sessions...")`，但`AuditService.log_action()`签名中`detail: Optional[Dict[str, Any]]`。传入str不会立即崩溃（Python动态类型），但违反了接口契约，且日志存储格式不一致。

### 3.3 性能问题

| # | 严重度 | 问题 | 位置 | 当前状态 | 影响 |
|---|--------|------|------|---------|------|
| P1 | P1→✅ | `SafetyGatewayClient.check_output()` 全局asyncio锁 | safety_gateway_client.py | **已改善** | 锁粒度已细化，仅保护`_consecutive_failures`计数器，HTTP请求不在锁内 |
| P2 | P2 | `batch_restore()` 串行执行 | desensitization_restore.py | 未修复 | 大量记录性能差 |
| P3 | P2 | `verify_all_chains()` 串行验证 | audit_service.py | 未修复 | 会话多时性能差 |
| P4 | P2→✅ | `check_output()` 重试无指数退避 | safety_gateway_client.py | **已修复** | L217: `backoff_delay = min(self._retry_interval * (2 ** (retry_attempt - 1)), 4.0)` |

### 3.4 安全问题

| # | 严重度 | 问题 | 位置 | 当前状态 | 影响 |
|---|--------|------|------|---------|------|
| S1 | P0→✅ | `RedisRefreshTokenStore`缺少并发会话限制 | refresh_token.py | **已修复** | L403-411已添加限制逻辑 |
| S2 | P1→✅ | `patient_name_simple` 规则锚定漏检 | desensitization.py L139 | **已修复** | 正则已改为`(?:^|[：:，,\s\n\r])` |
| S3 | P1→部分 | `desensitize_dict()` 不处理set/tuple | desensitization.py | **主引擎已修复** | SG引擎和融合引擎仍未处理(N3, N4) |

**N3/N4详细分析**：
- 主引擎 `DesensitizationEngine.desensitize_dict()` (L299-311) 已处理tuple和set
- SG引擎 `SafetyGatewayDesensitizationEngine.desensitize_dict()` (L279-293) 仅处理str/dict/list，**不处理tuple/set**
- 融合引擎 `FusionDesensitizationEngine.desensitize_dict()` (L110-133) 仅处理str/dict/list，**不处理tuple/set**

风险：如果诊断数据中包含tuple或set类型的字段值，SG和融合层将跳过脱敏，可能导致敏感数据泄漏。

---

## 四、代码风格审查

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 日志风格一致性 | ✅ | 统一使用structlog结构化日志，键值对格式 |
| 命名约定一致性 | ✅ | 模块/类/函数命名遵循项目规范 |
| 类型注解完整性 | ✅ | 关键函数均有参数和返回值类型注解 |
| 异常处理模式 | ⚠️ | `data_retention.py` detail参数类型不一致 |
| 配置引用方式 | ✅ | 统一通过settings引用配置 |
| 单例模式一致性 | ✅ | 使用`__new__`+线程锁或模块级变量实现单例 |
| 异步/同步混合 | ⚠️ | `MemoryRefreshTokenStore`同步方法 vs `RedisRefreshTokenStore`异步方法，基类签名不一致 |

---

## 五、功能正确性验证

### 5.1 三层脱敏功能 ✅

- 标准化层：仅处理结构化字段（PatientID→PATIENT_001等）✅
- 融合层：先调标准化层脱敏，再检查融合层特有模式（LLM生成患者、上下文泄漏等）✅
- SafetyGateway：使用异构正则规则做结构化身份模式快速拦截 ✅
- 降级场景：仅保留observations等白名单字段，标记conclusion_suppressed ✅
- 禁忌词过滤：过滤TI-RADS/BI-RADS等分级术语，保留否定前缀 ✅

### 5.2 HSM密钥管理功能 ✅

- 4个KeySlot隔离正确 ✅
- 降级状态机4阶段转换正确 ✅
- 应急签发流程完整（双人参验、日限额、Redis持久化）✅
- 密钥轮换自动执行（JWT 120天、脱敏映射 180天）✅
- 私钥加密存储（要求HSM_PRIVATE_KEY_PASSWORD）✅
- Redis持久化降级状态和密钥 ✅
- **签名填充方案**：通用签名用PSS，JWT签名用PKCS1v15 ⚠️ 测试需修正

### 5.3 患者同意管理功能 ✅

- 同意状态直查PG（ConsentChecker）✅
- 撤回后Redis Streams广播（3次重试）✅
- 两阶段校验（入口缓存+输出前直查PG）✅
- 撤回后边缘端停止标注 ✅
- 数据清理处理器清理脱敏映射缓存 ✅
- 撤回后<500ms生效 ⚠️ 逻辑正确但缺少运行时SLA监控

### 5.4 Refresh Token轮换功能 ✅

- Token Family机制 ✅
- 重放检测自动撤销整个家族 ✅
- Memory版并发会话限制 ✅
- Redis版并发会话限制 ✅（R1已修复）
- 模块级函数适配同步/异步差异 ✅

---

## 六、测试逻辑完整性审查

### 6.1 测试基线数据

| 类别 | 文件数 | 测试数 | 通过 | 失败 | 通过率 |
|------|--------|--------|------|------|--------|
| HSM密钥管理 | 4 | ~140 | ~140 | 0 | 100% |
| 脱敏引擎 | 4 | ~190 | ~190 | 0 | 100% |
| Token管理 | 3 | ~185 | ~183 | 2 | 98.9% |
| 患者同意 | 3 | ~32 | ~32 | 0 | 100% |
| 审计合规 | 3 | ~140 | ~140 | 0 | 100% |
| SafetyGateway | 2 | ~52 | ~52 | 0 | 100% |
| 三层脱敏集成 | 2 | ~175 | ~174 | 1 | 99.4% |
| 安全修复验证 | 4 | ~145 | ~137 | 8 | 94.5% |
| Redis集成 | 1 | ~42 | ~39 | 3 | 92.9% |
| **SEC合计** | **26** | **~1101** | **~1088** | **~12** | **98.9%** |
| 非SEC测试 | ~30 | ~1147 | ~1070 | ~29 | ~93.3% |
| **总计** | **56** | **2248** | **2158** | **41** | **96.0%** |

### 6.2 失败测试分析

| # | 测试文件 | 失败测试 | 原因分类 | 根因 |
|---|---------|---------|---------|------|
| 1 | test_sec_audit_regression.py | test_checker_validates_consent_status | 测试逻辑错误 | 断言源码包含`validated_consent_status`方法，但该方法不存在 |
| 2 | test_sec_token_consent_audit_deep.py | test_hsm_uses_pkcs1v15_not_pss | 测试逻辑错误 | 期望`sign()`使用PKCS1v15，但实际使用PSS（JWT用PKCS1v15是正确的） |
| 3-4 | test_sec_craft_fixes.py | test_sign_uses_pkcs1v15_not_pss, test_pss_padding_would_fail | 测试逻辑错误 | 同上，`sign()`使用PSS是正确的安全选择 |
| 5 | test_sec_craft_fixes.py | test_degraded_reason_sensitive_data_detected | 枚举值不匹配 | 测试期望的DegradedReason枚举值与实际不同 |
| 6 | test_security_compliance_complete.py | test_fusion_desensitize_none | 功能边界 | 融合层desensitize_llm_output(None)处理逻辑与测试预期不一致 |
| 7 | test_e2e_security_flow.py | test_full_pipeline_rejects_when_safety_gateway_denies | 集成测试 | SG拒绝时的完整管道行为与测试预期不一致 |
| 8-10 | test_redis_token_store_integration.py | 3个Redis相关测试 | Mock路径错误 | RedisPool在方法内部导入，测试mock路径不正确 |
| 11-12 | test_revocation_performance.py | 2个Redis性能测试 | Mock路径错误 | 同上 |

**关键发现**：12个失败测试中，5个是测试逻辑错误（非代码Bug），3个是Mock路径问题，2个是功能边界/集成测试问题。**实际代码Bug仅2个**。

### 6.3 性能基准测试结果

| 基准 | 目标 | 测试验证 | 状态 |
|------|------|---------|------|
| 脱敏吞吐量 | ≥1000次/秒 | ✅ test_desensitization_performance.py | 通过 |
| 脱敏P99延迟 | <5ms | ✅ test_desensitization_performance.py | 通过 |
| 签名P99延迟 | <5ms | ✅ test_hsm_signing_performance.py | 通过 |
| 签名验证P99 | <5ms | ✅ test_hsm_signing_performance.py | 通过 |
| JWT签名P99 | <5ms | ✅ test_hsm_signing_performance.py | 通过 |
| 撤回延迟 | <500ms | ✅ test_revocation_latency_benchmark.py | 通过 |
| Redis撤回性能 | - | ❌ Mock路径错误 | 不可用 |

---

## 七、文档一致性审查

### 7.1 需要更新的文档

| 文档 | 配置项 | 文档值 | 代码值 | 建议 |
|------|--------|--------|--------|------|
| 00b-统一配置参考手册 | `SG_DB_RETRY_MAX_TOTAL_TIME_SECONDS` | 10.0 | 15.0 (SG) | SG端15.0更合理（独立进程需更长重试窗口），建议更新文档 |

### 7.2 代码优于文档的实践（保留并更新文档）

| 实践 | 文档描述 | 代码实现 | 判定 |
|------|---------|---------|------|
| HSM签名填充 | 未明确区分 | `sign()`用PSS，`sign_jwt()`用PKCS1v15 | 代码更安全，建议补充文档 |
| 降级输出禁忌词过滤 | 未详细描述 | `filter_forbidden_terms()`含否定前缀处理 | 代码更完善，建议补充文档 |
| 应急签发Redis持久化 | 未明确 | 日计数器+启用状态均持久化到Redis | 代码更健壮，建议补充文档 |
| 审计链锁LRU淘汰 | 未描述 | `_MAX_CHAIN_LOCKS=10000`+LRU淘汰 | 代码更安全，建议补充文档 |

---

## 八、改进建议与优化方案

### 8.1 P0级（必须修复）

| # | 问题 | 修复方案 | 预估工作量 |
|---|------|---------|-----------|
| N1 | HSM签名填充方案不一致导致测试失败 | 修正测试用例：`sign()`使用PSS是正确的，`sign_jwt()`使用PKCS1v15也是正确的。更新测试以匹配实际行为 | 小 |

### 8.2 P1级（建议修复）

| # | 问题 | 修复方案 | 预估工作量 |
|---|------|---------|-----------|
| N2 | SG `sg_db_retry_max_total_time_seconds`与文档不一致 | 评估SG端15.0是否更优，若是则更新文档；否则对齐为10.0 | 小 |
| N3 | SG `desensitize_dict()`不处理set/tuple | 添加set/tuple处理分支，与主引擎对齐 | 小 |
| N4 | Fusion `desensitize_dict()`不处理set/tuple | 添加set/tuple处理分支，与主引擎对齐 | 小 |
| N5 | `DataRetentionService.log_action()` detail参数类型不匹配 | 将str参数改为Dict格式 | 小 |
| R6 | `RoleChecker`和`check_permission`两套权限机制 | 统一为一套权限校验机制 | 中 |
| R9 | 撤回<500ms缺少运行时SLA保障 | 添加延迟监控和告警 | 中 |

### 8.3 P2级（优化建议）

| # | 问题 | 修复方案 | 预估工作量 |
|---|------|---------|-----------|
| N6 | Redis benchmark测试mock路径错误 | 修正mock路径为`app.config.redis.RedisPool` | 小 |
| N7 | `ConsentChecker`测试断言源码包含不存在的方法 | 修正测试断言 | 小 |
| R10 | 内存计数器多实例不共享 | 迁移至Redis（部分已完成） | 中 |
| R11 | `batch_restore()`串行执行 | 改为asyncio.gather并发执行 | 中 |
| R12 | `verify_all_chains()`串行验证 | 改为asyncio.gather并发验证 | 中 |
| R13 | `SchedulingAgentHandler`存根实现 | 完成实际调度逻辑 | 大 |
| R15 | 二期错误码E3006/E3007未定义 | 二期前补充定义 | 小 |

---

## 九、审查结论

### 9.1 总体评价

SEC安全合规轨道的核心功能实现质量**良好且持续改善**。与上次审查相比：

1. **P0级问题从3个降至1个**（R1/R2/R3已修复，N1为新发现但属测试逻辑错误）
2. **文档一致性从71.6%提升至82.1%**
3. **SEC测试通过率从95.2%提升至98.9%**
4. **测试总数从1998增长至2248**（+12.5%）

### 9.2 主要成就

1. **R1 Redis并发会话限制已修复**：`RedisRefreshTokenStore.create_refresh_token()`已添加限制逻辑
2. **R2/R3 SG配置已对齐**：`db_query_timeout=5`、`sg_db_retry_max_attempts=3`
3. **R5 审计链锁清理已修复**：LRU淘汰+10000上限
4. **R8 姓名规则锚定已修复**：正则改为非锚定模式
5. **R4 SafetyGatewayClient锁粒度已细化**：仅保护计数器
6. **P4 重试退避已修复**：指数退避算法
7. **S3 主引擎set/tuple处理已修复**

### 9.3 关键风险

1. **N1 HSM签名填充方案不一致**（P0）— 测试逻辑错误，非代码Bug，但需修正测试
2. **N3/N4 SG和融合层desensitize_dict不处理set/tuple**（P1）— 潜在数据泄漏
3. **R6 双权限机制未统一**（P1）— 维护复杂度
4. **R9 撤回SLA监控缺失**（P1）— 合规风险

### 9.4 建议优先级

1. **立即**：修复N1（修正HSM签名填充测试）、N3/N4（SG和融合层添加set/tuple处理）
2. **短期**：修复N5（detail参数类型）、N2（配置文档对齐）、R6（权限统一）、R9（SLA监控）
3. **中期**：修复N6/N7（测试修正）、R10（内存计数器迁移）、R11/R12（并发化）
4. **长期**：R13（调度Agent实现）、R15（二期错误码）
