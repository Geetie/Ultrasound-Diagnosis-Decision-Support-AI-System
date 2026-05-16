# SEC安全合规轨道系统性审查计划 — 第三阶段：测试修复 + 深度审查 + 合规验证

## 当前状态总结

### 测试文件清单（21个主项目文件，约856条测试）
| 文件 | 测试数 | 状态 |
|------|--------|------|
| test_sec_hsm_deep.py | ~62 | ❌ verify_signature参数顺序错误 |
| test_sec_token_consent_audit_deep.py | ~129 | ❌ create_access_token签名不匹配、AuditService无参调用 |
| test_sec_desensitization_deep.py | ~127 | ❌ 部分PII规则与实际引擎不匹配 |
| test_sec_audit.py | ~37 | ✅ 基本通过 |
| test_sec_supplementary.py | ~51 | ✅ 基本通过 |
| test_sec_p0_critical.py | ~47 | ✅ 基本通过 |
| test_sec_craft_fixes.py | ~22 | ✅ 基本通过 |
| 其余14个文件 | ~381 | ✅ 已通过 |

### 已确认的源码级Bug（需同步修复）
1. **AuditService无参调用**（P0）：`emergency_signing.py` L107/L134/L247/L267 调用 `AuditService()` 无 `db` 参数，运行时必崩
2. **verify_signature参数顺序**（P0）：测试代码传 `(key_name, sig, data)` 但实际签名是 `(key_name, data, signature)`
3. **EmergencySigningManager无_is_active属性**（P1）：测试访问 `_is_active` 但实际用Redis异步方法 `is_emergency_signing_active()`
4. **create_access_token签名不匹配**（P1）：测试用 `create_access_token(user_id=..., role=...)` 但实际接受 `data: Dict`

---

## 执行步骤

### 步骤1：修复3个深度测试文件中的API签名不匹配（目标：消除59个测试失败）

#### 1.1 修复 test_sec_hsm_deep.py
- **verify_signature参数顺序**：所有 `verify_signature(key_name, sig, data)` → `verify_signature(key_name, data, sig)`
- 影响行：L48, L68, L69, L203, L249
- 验证：运行 `pytest tests/unit/test_sec_hsm_deep.py -v` 确认全部通过

#### 1.2 修复 test_sec_token_consent_audit_deep.py
- **create_access_token调用**：`create_access_token(user_id=..., role=...)` → `create_access_token({"sub": user_id, "role": role})`
- **AuditService实例化**：删除或mock所有 `AuditService()` 无参调用
- **EmergencySigningManager._is_active**：改为mock `is_emergency_signing_active()` 异步方法
- **错误码测试**：确保只使用 ERROR_CODE_MAP 中实际存在的错误码
- 验证：运行 `pytest tests/unit/test_sec_token_consent_audit_deep.py -v`

#### 1.3 修复 test_sec_desensitization_deep.py
- **PII规则匹配**：验证每种PII类型的正则与 `DesensitizationEngine` 实际规则一致
- **ThreeLayerDesensitizationManager**：确认导入路径和类名正确
- **SafetyGateway异构验证**：确保测试用例与 `SafetyGatewayDesensitizationEngine` 规则匹配
- 验证：运行 `pytest tests/unit/test_sec_desensitization_deep.py -v`

### 步骤2：修复源码级Bug（与测试修复同步进行）

#### 2.1 修复 AuditService 无参调用（P0）
- 文件：`app/security/emergency_signing.py`
- L107/L134/L247/L267：`AuditService()` → 移除直接实例化，改为依赖注入或通过事件记录方式
- 方案：将审计日志记录改为 `structlog` 结构化日志 + 安全事件表直接写入，避免 AuditService 的 db 依赖

#### 2.2 确认 verify_signature 调用链正确性
- 检查所有源码中 `verify_signature` 的调用是否与签名 `(key_name, data, signature)` 一致
- 特别检查 `emergency_signing.py` L236 的调用

### 步骤3：运行全量测试，确认CI≥1000条目标

- 运行 `pytest tests/unit/ -v --co -q` 统计总测试数
- 运行 `pytest tests/unit/ -v --tb=short` 确认全部通过
- 若总数不足1000，在现有测试文件中补充参数化测试用例（优先补充边界条件和异常场景）

### 步骤4：设计规范一致性审查（对照核心参考文档）

#### 4.1 P0文档对照
- **03-接口规范.md**：
  - 错误码体系：验证代码中 ERROR_CODE_MAP 与文档定义一致
  - 数据库表结构：验证 ORM 模型与文档定义的字段一致
  - API限流：验证令牌桶实现与文档规格一致

- **01-系统架构设计.md**：
  - SafetyGateway独立进程：验证 `safety_gateway/` 实现与文档规格一致
  - 三道防线职责分层：验证三层脱敏各层职责与文档定义一致
  - 降级策略：验证降级输出行为与文档降级策略总表一致

#### 4.2 P1文档对照
- **02-核心流程与知识库.md**：验证 observations 白名单字段与代码实现一致
- **00a-术语表.md**：验证错误码、状态枚举与代码定义一致

#### 4.3 P2文档对照
- **04-安全合规.md**：验证脱敏规则、数据保留策略、认证机制与实现一致
- **00b-统一配置参考手册.md**：验证配置项名称、默认值与代码一致

### 步骤5：代码质量深度审查

#### 5.1 健壮性
- AuditService `_chain_locks` 字典无清理机制 → 内存泄漏风险
- EmergencySigningManager `_daily_signature_count` 内存计数器 → 进程重启归零
- DesensitizationEngine `_patient_token_counter` 内存状态 → 多实例不共享

#### 5.2 可维护性
- `RoleChecker` 和 `check_permission` 两套独立权限校验机制未统一
- `MemoryRefreshTokenStore` 同步方法 vs `RedisRefreshTokenStore` 异步方法 → 接口不一致

#### 5.3 性能
- SafetyGatewayClient `check_output()` 全局锁 → 高并发瓶颈
- `batch_restore()` 串行执行 → 大量记录性能差
- `verify_all_chains()` 串行验证 → 会话多时性能差

### 步骤6：功能正确性验证

#### 6.1 三层脱敏
- 验证标准化层仅处理结构化字段，不处理自由文本
- 验证融合层仅处理LLM生成的自由文本
- 验证SafetyGateway仅做结构化身份模式快速拦截
- 验证双重降级场景输出行为（仅observations白名单）

#### 6.2 HSM密钥管理
- 验证4个KeySlot隔离正确
- 验证降级状态机4阶段转换正确
- 验证密钥轮换120天/180天周期
- 验证SoftHSM 90天超期告警

#### 6.3 患者同意管理
- 验证同意状态直查PostgreSQL（不使用缓存）
- 验证撤回后Redis Streams广播
- 验证撤回后<500ms生效
- 验证两阶段校验（调度层缓存+输出前直查）

#### 6.4 Refresh Token轮换
- 验证Token Family机制
- 验证重放检测自动撤销整个家族
- 验证并发会话限制
- 验证RedisRefreshTokenStore也有并发会话限制

### 步骤7：性能基准验证

- 签名P99 < 5ms：运行 `test_desensitization_performance.py` 确认
- 撤回后 < 500ms生效：运行相关测试确认
- CI ≥ 1000条测试：统计确认

### 步骤8：生成审查报告

输出审查报告至 `.trae/documents/SEC安全合规轨道审查报告.md`，包含：
1. 审查范围与方法
2. 发现问题清单（按P0/P1/P2分级）
3. 已修复问题及验证结果
4. 遗留风险与改进建议
5. 测试覆盖率统计
6. 文档一致性审查结果
7. 性能基准验证结果

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 修复测试可能引入新失败 | 每修一个文件立即运行该文件测试验证 |
| AuditService无参调用修复可能影响其他模块 | 先搜索所有AuditService()调用点，统一修复 |
| PII规则与引擎不匹配 | 先读取引擎默认规则，再调整测试用例 |
| 测试总数不足1000 | 优先补充参数化边界条件测试，而非新增测试类 |
