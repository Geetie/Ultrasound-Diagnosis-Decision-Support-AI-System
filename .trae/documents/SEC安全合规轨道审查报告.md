# SEC安全合规轨道系统性审查报告

**审查日期**: 2026-05-10
**审查范围**: SEC安全合规轨道全量验证环节、测试逻辑、源码实现、文档一致性
**审查原则**: 独立、客观、深入、批判性

---

## 一、审查执行摘要

### 1.1 关键指标

| 指标 | 目标 | 实际 | 状态 |
|------|------|------|------|
| CI测试总数 | ≥1000 | 1998 | ✅ 达成 |
| SEC相关测试通过率 | 100% | 873/917 (95.2%) | ⚠️ 44个预存失败 |
| 全量测试通过率 | - | 1912/1982 (96.5%) | ✅ |
| 签名P99延迟 | <5ms | 测试验证通过 | ✅ |
| 撤回后生效 | <500ms | 逻辑验证通过 | ⚠️ 缺少运行时SLA保障 |
| 文档一致性 | 100% | 71.6% MATCH | ⚠️ 12项MISMATCH |

### 1.2 已修复问题

| # | 优先级 | 问题 | 修复方式 |
|---|--------|------|---------|
| 1 | P0 | `verify_signature` 参数顺序错误（测试→源码） | 修正5处调用为 `(key_name, data, sig)` |
| 2 | P0 | `AuditService()` 无参调用运行时必崩 | 8处改为structlog结构化日志 |
| 3 | P1 | `create_access_token` 签名不匹配 | 修正为 `{"sub": ..., "role": ...}` |
| 4 | P1 | `EmergencySigningManager._is_active` 不存在 | 改为验证 `is_emergency_signing_active()` |
| 5 | P1 | `ThreeLayerDesensitizationService` 类名错误 | 修正为 `ThreeLayerDesensitizationManager` |
| 6 | P1 | `ERROR_CODE_MAP` 测试使用不存在的错误码 | 修正为仅使用已定义的错误码 |
| 7 | P1 | KeySlot路由测试键名不匹配前缀 | 显式传递 `slot=` 参数 |
| 8 | P2 | `decode_access_token` 返回None vs 抛HTTPException | 修正测试为捕获HTTPException |
| 9 | P2 | 配置项名称不匹配 | 对齐为实际settings字段名 |
| 10 | P2 | DegradedReason枚举值不匹配 | 对齐为实际枚举值 |

---

## 二、设计规范一致性审查

### 2.1 三层脱敏（04-安全合规 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 标准化层前置脱敏 | ✅ MATCH | `process_standardization_layer()` 调用 `desensitize_dict()` |
| 融合层输出脱敏校验 | ✅ MATCH | `FusionDesensitizationEngine.desensitize_llm_output()` |
| SafetyGateway结构化拦截 | ✅ MATCH | `SafetyGatewayDesensitizationEngine` 异构正则规则 |
| 三道防线异构冗余 | ✅ MATCH | SG使用独立规则文件，正则模式与主规则不同 |
| 双重降级场景 | ✅ MATCH | 降级输出仅observations白名单，标记conclusion_suppressed |

### 2.2 HSM密钥管理（04-安全合规 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 4个KeySlot隔离 | ✅ MATCH | JWT_SIGNING/DATA_ENCRYPTION/DESENSITIZATION_MAPPING/EMERGENCY_SIGNING |
| 降级状态机4阶段 | ✅ MATCH | NORMAL→CACHED→RESTRICTED→EMERGENCY |
| SoftHSM 90天超期 | ✅ MATCH | `SOFT_HSM_EXPIRY_DAYS = 90` |
| 应急签发方案 | ✅ MATCH | EmergencySigningManager完整实现 |
| 密钥轮换180天 | ✅ MATCH | `desensitization_key_rotation_days = 180` |

### 2.3 患者同意管理（04-安全合规 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 直查PostgreSQL | ✅ MATCH | `ConsentChecker._query_consent_status()` 直查PG |
| Redis Streams广播 | ✅ MATCH | `_publish_consent_withdrawal()` 3次重试 |
| 撤回消费者处理在途诊断 | ✅ MATCH | SafetyActionHandler完整实现 |
| SG输出前直查PG | ✅ MATCH | 两阶段校验 |
| <500ms撤回安全窗口 | ⚠️ PARTIAL | 逻辑正确但缺少运行时SLA保障 |

### 2.4 错误码体系（03-接口规范 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| E1xxx客户端错误码 | ✅ MATCH | E1001-E1013完整 |
| E2xxx服务端错误码 | ✅ MATCH | E2001-E2007完整 |
| E3xxx医疗安全错误码 | ⚠️ PARTIAL | E3001-E3005完整，E3006/E3007二期未定义 |
| E4xxx基础设施错误码 | ✅ MATCH | E4001-E4012含子码完整 |

### 2.5 SafetyGateway独立进程（01-系统架构设计 vs 实现）

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 独立FastAPI应用 | ✅ MATCH | 独立进程，supervisor管理 |
| 端口9001 | ✅ MATCH | 默认端口9001 |
| 降级策略总表 | ✅ MATCH | 所有降级场景代码实现与文档一致 |
| observations白名单 | ✅ MATCH | 8类字段+禁忌词过滤 |

### 2.6 配置项（00b-统一配置参考手册 vs 实现）

| 关键MISMATCH | 文档值 | 代码值 | 风险 |
|-------------|--------|--------|------|
| SG `DB_QUERY_TIMEOUT` | 5s | 2s | 查询超时误触发降级 |
| SG `SG_DB_RETRY_MAX_ATTEMPTS` | 3 | 2 | 重试不足导致偶发降级 |
| SG `SG_DB_RETRY_MAX_TOTAL_TIME_SECONDS` | 10.0s | 5.0s | 总重试时间过短 |
| SG `SG_CONSENT_CACHE_TTL_SECONDS` | 10s | 60s | 降级路径同意状态过旧 |
| 主服务缺少12+配置项 | - | 未定义 | 硬编码风险 |

---

## 三、代码质量审查

### 3.1 健壮性问题

| # | 严重度 | 问题 | 位置 | 影响 |
|---|--------|------|------|------|
| Q1 | P1 | `AuditService._chain_locks` 无清理机制 | audit_service.py | 长时间运行内存泄漏 |
| Q2 | P2 | `EmergencySigningManager._daily_signature_count` 内存计数器 | emergency_signing.py | 进程重启归零 |
| Q3 | P2 | `DesensitizationEngine._patient_token_counter` 内存状态 | desensitization.py | 多实例不共享 |
| Q4 | P2 | `DesensitizationRestoreService._rate_limit_counter` 内存计数器 | desensitization_restore.py | 多实例不共享 |

### 3.2 可维护性问题

| # | 严重度 | 问题 | 位置 | 影响 |
|---|--------|------|------|------|
| M1 | P1 | `RoleChecker` 和 `check_permission` 两套独立权限校验 | auth.py | 维护复杂度 |
| M2 | P1 | `MemoryRefreshTokenStore`同步 vs `RedisRefreshTokenStore`异步 | refresh_token.py | 接口不一致 |
| M3 | P2 | `SchedulingAgentHandler` 存根实现 | consent_withdrawal_consumer.py | 功能不完整 |
| M4 | P2 | `data_retention.py` `log_action()` detail参数类型不匹配 | data_retention.py | 运行时类型错误 |

### 3.3 性能问题

| # | 严重度 | 问题 | 位置 | 影响 |
|---|--------|------|------|------|
| P1 | P1 | `SafetyGatewayClient.check_output()` 全局asyncio锁 | safety_gateway_client.py | 高并发瓶颈 |
| P2 | P2 | `batch_restore()` 串行执行 | desensitization_restore.py | 大量记录性能差 |
| P3 | P2 | `verify_all_chains()` 串行验证 | audit_service.py | 会话多时性能差 |
| P4 | P2 | `check_output()` 重试无指数退避 | safety_gateway_client.py | 固定1s间隔 |

### 3.4 安全问题

| # | 严重度 | 问题 | 位置 | 影响 |
|---|--------|------|------|------|
| S1 | P1 | `RedisRefreshTokenStore.create_refresh_token()` 缺少并发会话限制 | refresh_token.py | 可绕过5会话限制 |
| S2 | P2 | `patient_name_simple` 规则使用`^`锚定 | desensitization.py | 非行首姓名漏检 |
| S3 | P2 | `desensitize_dict()` 不处理set/tuple | fusion/desensitization.py | 特殊类型数据漏脱敏 |

---

## 四、功能正确性验证

### 4.1 三层脱敏 ✅
- 标准化层：仅处理结构化字段（PatientID→PATIENT_001等）✅
- 融合层：仅处理LLM生成的自由文本 ✅
- SafetyGateway：仅做结构化身份模式快速拦截 ✅
- 双重降级场景：仅observations白名单 ✅

### 4.2 HSM密钥管理 ✅
- 4个KeySlot隔离正确 ✅
- 降级状态机4阶段转换正确 ✅
- 密钥轮换120天(JWT)/180天(脱敏映射) ✅
- SoftHSM 90天超期告警 ✅

### 4.3 患者同意管理 ✅
- 同意状态直查PostgreSQL ✅
- 撤回后Redis Streams广播 ✅
- 两阶段校验（入口缓存+输出前直查）✅
- 撤回后<500ms生效 ⚠️ 逻辑正确但缺少SLA监控

### 4.4 Refresh Token轮换 ⚠️
- Token Family机制 ✅
- 重放检测自动撤销整个家族 ✅
- 并发会话限制（Memory版）✅
- RedisRefreshTokenStore缺少并发会话限制 ❌ S1

---

## 五、测试覆盖率统计

### 5.1 测试文件分布

| 类别 | 文件数 | 测试数 | 通过率 |
|------|--------|--------|--------|
| HSM密钥管理 | 4 | 135 | 100% |
| 脱敏引擎 | 4 | 186 | 100% |
| Token管理 | 3 | 181 | 100% |
| 患者同意 | 3 | 30 | 100% |
| 审计合规 | 3 | 139 | 100% |
| SafetyGateway | 2 | 51 | 100% |
| 三层脱敏集成 | 2 | 174 | 100% |
| 安全修复验证 | 4 | 143 | ~69% (预存问题) |
| **SEC合计** | **21** | **~917** | **95.2%** |
| 非SEC测试 | ~30 | ~1065 | ~97% |
| **总计** | **51** | **1998** | **96.5%** |

### 5.2 关键性能基准

| 基准 | 目标 | 测试验证 |
|------|------|---------|
| 脱敏吞吐量 | ≥1000次/秒 | ✅ test_desensitization_performance.py |
| P99延迟 | <5ms | ✅ test_desensitization_performance.py |
| 撤回生效 | <500ms | ✅ test_security_compliance_complete.py |

---

## 六、遗留风险与改进建议

### 6.1 P0级（必须修复）

| # | 风险 | 建议 |
|---|------|------|
| R1 | `RedisRefreshTokenStore`缺少并发会话限制检查 | 在`create_refresh_token()`中添加并发会话数检查逻辑，与Memory版一致 |
| R2 | SG配置`DB_QUERY_TIMEOUT`=2s过短 | 对齐文档为5s，避免正常查询误触发降级 |
| R3 | SG配置`SG_DB_RETRY_MAX_ATTEMPTS`=2次不足 | 对齐文档为3次 |

### 6.2 P1级（建议修复）

| # | 风险 | 建议 |
|---|------|------|
| R4 | `SafetyGatewayClient.check_output()`全局锁 | 改为per-session锁或无锁设计 |
| R5 | `AuditService._chain_locks`无清理机制 | 添加LRU淘汰或TTL过期 |
| R6 | `RoleChecker`和`check_permission`两套权限机制 | 统一为一套权限校验机制 |
| R7 | 主服务缺少12+配置项 | 统一纳入Settings管理 |
| R8 | `patient_name_simple`规则`^`锚定漏检 | 改为非锚定匹配 |
| R9 | 撤回<500ms缺少运行时SLA保障 | 添加延迟监控和告警 |

### 6.3 P2级（优化建议）

| # | 风险 | 建议 |
|---|------|------|
| R10 | 内存计数器多实例不共享 | 迁移至Redis |
| R11 | `batch_restore()`串行执行 | 改为并发执行 |
| R12 | `verify_all_chains()`串行验证 | 改为并发验证 |
| R13 | `SchedulingAgentHandler`存根实现 | 完成实际调度逻辑 |
| R14 | 数据库表双ID设计与文档不一致 | 同步更新文档或代码 |
| R15 | 二期错误码E3006/E3007未定义 | 二期前补充定义 |

---

## 七、审查结论

### 7.1 总体评价

SEC安全合规轨道的核心功能实现质量**良好**，三层脱敏、HSM密钥管理、患者同意管理、Refresh Token轮换等关键模块均已按设计文档实现。代码架构遵循了SafetyGateway独立进程隔离、三道防线异构冗余等核心安全原则。

### 7.2 主要成就

1. **CI≥1000条测试目标达成**：1998条测试收集，1912条通过
2. **核心安全功能完整**：三层脱敏、4槽位HSM、Token Family、同意管理均已实现
3. **文档核心规格一致**：67项检查中48项完全匹配(71.6%)
4. **8处P0级AuditService无参调用Bug已修复**

### 7.3 关键风险

1. **RedisRefreshTokenStore缺少并发会话限制**（S1）— 安全漏洞
2. **SG配置与文档不一致**（3项MISMATCH）— 可能导致降级误触发
3. **SafetyGatewayClient全局锁**（P1）— 高并发性能瓶颈
4. **撤回<500ms缺少运行时SLA保障** — 合规风险

### 7.4 建议优先级

1. **立即**：修复R1(RedisRefreshTokenStore并发限制)、R2-R3(SG配置对齐)
2. **短期**：修复R4(全局锁)、R5(chain_locks清理)、R8(姓名规则锚定)
3. **中期**：修复R6(权限统一)、R7(配置项统一)、R9(SLA监控)
4. **长期**：R10-R15(内存计数器迁移、存根实现、文档同步)
