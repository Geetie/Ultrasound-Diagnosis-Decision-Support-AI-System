# SEC安全合规轨道系统性审查计划

## 审查目标

对SEC轨道的所有验证环节和测试逻辑进行全面、系统性审查，确保其正确有效性。审查覆盖设计规范、代码质量、代码风格、功能正确性四大维度，并对照核心参考文档进行一致性审查。

---

## 审查范围

### SEC轨道核心模块

| 模块 | 源码位置 | 关键功能 |
|------|---------|---------|
| 三层脱敏 | `app/security/desensitization.py`, `app/security/three_layer_desensitization.py`, `app/fusion/desensitization.py`, `safety_gateway/desensitization.py` | 标准化层→融合层→SafetyGateway三道防线 |
| HSM密钥管理 | `app/security/hsm.py`, `app/security/emergency_signing.py` | 4槽位隔离、降级状态机、应急签发 |
| 患者同意管理 | `app/security/consent_withdrawal_consumer.py`, `safety_gateway/consent_checker.py` | 直查PG、Redis Streams广播、撤回处理 |
| Refresh Token轮换 | `app/security/refresh_token.py` | Token Family、重放检测、并发会话限制 |
| 审计链 | `app/services/audit_service.py` | 哈希链完整性、链锁管理 |
| SafetyGateway客户端 | `app/services/safety_gateway_client.py` | 降级策略、重试、快速失败 |
| 安全API | `app/api/v1/auth.py`, `app/api/v1/consent.py`, `app/api/v1/security.py` | 认证、同意、安全端点 |

### 核心参考文档

| 优先级 | 文档 | 关键内容 |
|--------|------|---------|
| P0 | 03-接口规范.md | DICOM接口、WebSocket双连接、诊断接口、错误码体系、数据库表结构 |
| P0 | 01-系统架构设计.md | WebSocket连接设计、SafetyGateway集成、边缘端通信规范 |
| P1 | 02-核心流程与知识库.md | 核心流程、会话状态、版本管理、响应路径定义 |
| P1 | 00a-术语表.md | 错误码、状态枚举、接口术语统一定义 |
| P2 | 04-安全合规.md | 脱敏规则、数据保留策略、认证机制 |
| P2 | 00b-统一配置参考手册.md | API相关配置项 |

---

## 审查步骤

### 步骤1：运行现有测试套件，建立基线

**目标**：获取当前测试通过率、测试数量、性能基准的精确数据

**操作**：
1. 运行 `pytest tests/ --tb=short -q` 获取全量测试结果
2. 运行 `pytest tests/benchmark/ -v` 获取性能基准数据
3. 统计SEC相关测试文件数、测试用例数、通过/失败/跳过数
4. 记录签名P99延迟、脱敏吞吐量、撤回延迟等关键指标

**验证**：
- CI测试总数 ≥ 1000
- SEC测试通过率
- 签名P99 < 5ms
- 撤回后生效 < 500ms

---

### 步骤2：设计规范一致性审查

**目标**：验证实现与设计文档的一致性

#### 2.1 三层脱敏一致性（对照04-安全合规.md）

| 检查项 | 验证方法 |
|--------|---------|
| 标准化层前置脱敏 | 检查 `ThreeLayerDesensitizationManager.process_standardization_layer()` 是否调用 `desensitize_dict()` |
| 融合层输出脱敏校验 | 检查 `FusionDesensitizationEngine.desensitize_llm_output()` 实现 |
| SafetyGateway结构化拦截 | 检查 `SafetyGatewayDesensitizationEngine` 异构正则规则 |
| 三道防线异构冗余 | 验证SG规则文件与主规则文件正则模式不同 |
| 双重降级场景 | 验证降级输出仅observations白名单，标记conclusion_suppressed |

#### 2.2 HSM密钥管理一致性（对照04-安全合规.md）

| 检查项 | 验证方法 |
|--------|---------|
| 4个KeySlot隔离 | 检查 `KeySlot` 枚举定义完整性 |
| 降级状态机4阶段 | 检查 `DegradedPhase` 枚举和 `DEGRADED_BEHAVIORS` 映射 |
| SoftHSM 90天超期 | 检查 `SOFT_HSM_EXPIRY_DAYS = 90` |
| 应急签发方案 | 检查 `EmergencySigningManager` 完整实现 |
| 密钥轮换周期 | 检查 `jwt_key_rotation_days=120`, `desensitization_key_rotation_days=180` |

#### 2.3 患者同意管理一致性（对照04-安全合规.md）

| 检查项 | 验证方法 |
|--------|---------|
| 直查PostgreSQL | 检查 `ConsentChecker._query_consent_status()` |
| Redis Streams广播 | 检查 `_publish_consent_withdrawal()` 重试逻辑 |
| 撤回消费者处理在途诊断 | 检查 `SafetyActionHandler` 实现 |
| SG输出前直查PG | 检查两阶段校验逻辑 |
| <500ms撤回安全窗口 | 检查撤回延迟测试和运行时SLA保障 |

#### 2.4 错误码体系一致性（对照03-接口规范.md）

| 检查项 | 验证方法 |
|--------|---------|
| E1xxx客户端错误码 | 检查 `ERROR_CODE_MAP` 中E1001-E1013 |
| E2xxx服务端错误码 | 检查E2001-E2007 |
| E3xxx医疗安全错误码 | 检查E3001-E3005，确认E3006/E3007状态 |
| E4xxx基础设施错误码 | 检查E4001-E4012含子码 |

#### 2.5 配置项一致性（对照00b-统一配置参考手册.md）

| 检查项 | 验证方法 |
|--------|---------|
| SG `DB_QUERY_TIMEOUT` | 对比文档5s vs 代码值 |
| SG `SG_DB_RETRY_MAX_ATTEMPTS` | 对比文档3 vs 代码值 |
| SG `SG_DB_RETRY_MAX_TOTAL_TIME_SECONDS` | 对比文档10.0s vs 代码值 |
| SG `SG_CONSENT_CACHE_TTL_SECONDS` | 对比文档10s vs 代码值 |
| 主服务缺失配置项 | 检查Settings中是否定义文档中的所有配置项 |

**验证**：每个检查项标记MATCH/MISMATCH/PARTIAL，记录具体差异

---

### 步骤3：代码质量审查

**目标**：分析代码健壮性、可维护性、可扩展性和性能表现

#### 3.1 健壮性问题排查

| 检查项 | 风险 | 验证方法 |
|--------|------|---------|
| `AuditService._chain_locks` 清理机制 | 内存泄漏 | 检查LRU淘汰逻辑是否正确 |
| `EmergencySigningManager._daily_signature_count` | 进程重启归零 | 检查Redis持久化是否生效 |
| `DesensitizationEngine._patient_token_counter` | 多实例不共享 | 检查是否有Redis替代方案 |
| `DesensitizationRestoreService._rate_limit_counter` | 多实例不共享 | 检查是否有Redis替代方案 |

#### 3.2 可维护性问题排查

| 检查项 | 风险 | 验证方法 |
|--------|------|---------|
| `RoleChecker` vs `check_permission` 双权限机制 | 维护复杂度 | 检查两套机制是否统一 |
| `MemoryRefreshTokenStore`同步 vs `RedisRefreshTokenStore`异步 | 接口不一致 | 检查基类方法签名 |
| `SchedulingAgentHandler` 存根实现 | 功能不完整 | 检查是否有实际调度逻辑 |
| `data_retention.py` 类型不匹配 | 运行时错误 | 检查 `log_action()` detail参数类型 |

#### 3.3 性能问题排查

| 检查项 | 风险 | 验证方法 |
|--------|------|---------|
| `SafetyGatewayClient.check_output()` 全局asyncio锁 | 高并发瓶颈 | 检查锁粒度是否可优化 |
| `batch_restore()` 串行执行 | 大量记录性能差 | 检查是否可并发化 |
| `verify_all_chains()` 串行验证 | 会话多时性能差 | 检查是否可并发化 |
| `check_output()` 重试退避策略 | 固定间隔不够优 | 检查当前退避算法 |

#### 3.4 安全问题排查

| 检查项 | 风险 | 验证方法 |
|--------|------|---------|
| `RedisRefreshTokenStore` 并发会话限制 | 可绕过5会话限制 | 检查 `create_refresh_token()` 中的限制逻辑 |
| `patient_name_simple` 规则锚定 | 非行首姓名漏检 | 检查正则表达式 |
| `desensitize_dict()` 不处理set/tuple | 特殊类型漏脱敏 | 检查类型处理分支 |

**验证**：每个问题标记P0/P1/P2优先级，记录影响范围和修复建议

---

### 步骤4：代码风格审查

**目标**：检查编码规范、命名约定和文档标准的一致性

| 检查项 | 验证方法 |
|--------|---------|
| 日志风格一致性 | 检查是否统一使用structlog结构化日志 |
| 命名约定一致性 | 检查模块/类/函数命名是否遵循项目规范 |
| 类型注解完整性 | 检查关键函数的参数和返回值类型注解 |
| 异常处理模式 | 检查异常处理是否遵循项目统一模式 |
| 配置引用方式 | 检查是否统一通过settings引用配置 |

---

### 步骤5：功能正确性验证

**目标**：验证所有功能按需求规格正确实现，边界条件和异常处理完善

#### 5.1 三层脱敏功能验证

| 测试场景 | 验证方法 |
|---------|---------|
| 标准化层仅处理结构化字段 | 运行单元测试，检查PatientID→PATIENT_001映射 |
| 融合层仅处理LLM自由文本 | 运行单元测试，检查NER+规则增强 |
| SafetyGateway结构化拦截 | 运行单元测试，检查异构正则匹配 |
| 降级场景仅observations白名单 | 运行集成测试，检查降级输出 |
| 边界：空输入/None/空字典 | 运行边界测试 |

#### 5.2 HSM密钥管理功能验证

| 测试场景 | 验证方法 |
|---------|---------|
| 4个KeySlot隔离正确 | 运行test_hsm_key_slot.py |
| 降级状态机4阶段转换 | 运行test_sec_hsm_deep.py |
| 应急签发流程 | 运行test_emergency_signing.py |
| 密钥轮换 | 运行相关测试 |
| 边界：HSM不可用时的降级行为 | 运行降级测试 |

#### 5.3 患者同意管理功能验证

| 测试场景 | 验证方法 |
|---------|---------|
| 同意状态直查PG | 运行test_consent_api.py |
| 撤回后Redis Streams广播 | 运行test_consent_withdrawal_consumer.py |
| 两阶段校验 | 运行集成测试 |
| 撤回后<500ms生效 | 运行性能基准测试 |
| 边界：并发撤回/网络分区 | 运行边界测试 |

#### 5.4 Refresh Token轮换功能验证

| 测试场景 | 验证方法 |
|---------|---------|
| Token Family机制 | 运行test_refresh_token.py |
| 重放检测自动撤销家族 | 运行重放检测测试 |
| Memory版并发会话限制 | 运行并发限制测试 |
| Redis版并发会话限制 | 运行Redis集成测试 |
| 边界：Token过期/并发轮换 | 运行边界测试 |

---

### 步骤6：测试逻辑完整性审查

**目标**：评估测试覆盖率、边界条件覆盖、异常场景处理

| 检查项 | 验证方法 |
|--------|---------|
| SEC测试总数 ≥ 1000 | 统计pytest收集结果 |
| 每个SEC模块有独立测试文件 | 检查tests目录结构 |
| 边界条件测试覆盖 | 检查boundary_test_cases.py |
| 异常场景测试覆盖 | 检查错误处理测试 |
| 性能基准测试覆盖 | 检查benchmark测试文件 |
| 集成测试覆盖 | 检查integration测试文件 |
| 预存失败测试分析 | 分析44个预存失败原因 |

---

### 步骤7：文档一致性审查与更新

**目标**：对照核心参考文档进行一致性审查，保留更优秀的编码实践并更新文档

| 检查项 | 验证方法 |
|--------|---------|
| 配置项MISMATCH对齐 | 对比代码值与文档值，确定以哪个为准 |
| 错误码MISMATCH对齐 | 对比代码定义与文档定义 |
| 接口规范MISMATCH对齐 | 对比API实现与文档描述 |
| 保留更优编码实践 | 评估代码实现是否优于文档规范，若是则更新文档 |

---

### 步骤8：生成审查报告

**目标**：汇总所有发现，生成结构化审查报告

**报告内容**：
1. 审查执行摘要（关键指标、已修复问题、遗留风险）
2. 设计规范一致性审查结果
3. 代码质量审查结果
4. 代码风格审查结果
5. 功能正确性验证结果
6. 测试逻辑完整性评估
7. 文档一致性审查结果
8. 改进建议与优化方案（按P0/P1/P2分级）

---

## 上次审查遗留问题追踪

根据 `.trae/documents/SEC安全合规轨道审查报告.md`，上次审查发现以下遗留问题：

### P0级（必须修复）
| # | 问题 | 当前状态 | 本轮验证 |
|---|------|---------|---------|
| R1 | `RedisRefreshTokenStore`缺少并发会话限制检查 | 代码已有`create_refresh_token()`中的限制逻辑（第403-411行），需验证是否完整 | 重新审查 |
| R2 | SG配置`DB_QUERY_TIMEOUT`=2s过短 | sg_config.py已设为5s，与文档一致 | 已修复，验证 |
| R3 | SG配置`SG_DB_RETRY_MAX_ATTEMPTS`=2次不足 | sg_config.py已设为3次，与文档一致 | 已修复，验证 |

### P1级（建议修复）
| # | 问题 | 当前状态 | 本轮验证 |
|---|------|---------|---------|
| R4 | `SafetyGatewayClient.check_output()`全局锁 | 代码仍使用`self._lock`（asyncio.Lock），但已改为细粒度使用（仅保护`_consecutive_failures`） | 重新评估 |
| R5 | `AuditService._chain_locks`无清理机制 | 代码已实现LRU淘汰（`_MAX_CHAIN_LOCKS=10000`） | 已修复，验证 |
| R6 | `RoleChecker`和`check_permission`两套权限机制 | 待验证 | 审查 |
| R7 | 主服务缺少12+配置项 | 待验证 | 审查 |
| R8 | `patient_name_simple`规则`^`锚定漏检 | 待验证 | 审查 |
| R9 | 撤回<500ms缺少运行时SLA保障 | 待验证 | 审查 |

### P2级（优化建议）
| # | 问题 | 当前状态 | 本轮验证 |
|---|------|---------|---------|
| R10 | 内存计数器多实例不共享 | 待验证 | 审查 |
| R11 | `batch_restore()`串行执行 | 待验证 | 审查 |
| R12 | `verify_all_chains()`串行验证 | 待验证 | 审查 |
| R13 | `SchedulingAgentHandler`存根实现 | 代码确认仍为存根 | 审查 |
| R14 | 数据库表双ID设计与文档不一致 | 待验证 | 审查 |
| R15 | 二期错误码E3006/E3007未定义 | 待验证 | 审查 |

---

## 审查工具与方法

1. **静态分析**：阅读源码，对照文档逐项检查
2. **动态验证**：运行测试套件，验证功能正确性和性能指标
3. **对比审查**：代码实现 vs 文档规范，标记MATCH/MISMATCH
4. **边界测试**：针对边界条件和异常场景设计测试用例
5. **性能基准**：运行benchmark测试，验证P99延迟和吞吐量

---

## 预期产出

1. 更新后的SEC安全合规轨道审查报告（`.trae/documents/SEC安全合规轨道审查报告_v2.md`）
2. 发现问题的修复代码变更
3. 文档一致性更新（如需要）
4. 新增测试用例（如发现覆盖缺口）
