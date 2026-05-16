# SEC安全合规轨道系统性审查计划（第二阶段）

## 一、审查背景与当前状态

### 已完成的工作（第一阶段）
- 静态代码审查已完成，发现并修复了12项关键问题
- 测试套件首次运行：73 failed, 268 passed, 8 errors（349条）
- 已修复的源码问题：
  - `data_retention.py`：`kb_version` → `knowledge_base_version`，补充住院30年保留期检查
  - `audit_service.py`：全局锁改为per-session分锁，新增`verify_all_chains`
  - `schemas.py`：`ConsentUpdateRequest`增加`operator_id`，`granted_at`增加`consented_at`别名
  - `fusion/desensitization.py`：统一替换改为差异化`_replacement_rules`，新增LLM生成身份证/手机号检测
  - `desensitization_rules_safety.yaml`：手机号规则改为整体匹配（异构），新增`sg_ip_address`
  - `safety_gateway/desensitization.py`：同步YAML的手机号和IP规则变更
  - `errors.py`：HTTP 403映射从E1004改为E1008
  - `refresh_token.py`：新增`rotate_refresh_token`方法（P0功能缺失）
  - `conftest.py`：新增`HSM_PRIVATE_KEY_PASSWORD` autouse fixture
- 已修复的测试问题：
  - 脱敏测试期望值从`[REDACTED_NAME]`更新为`PATIENT_XXX`匿名令牌模式
  - `desensitization_restore`测试改为async
  - `sec_audit.py`：降级签名错误消息大小写、CircuitBreaker改async、Consent API权限检查逻辑、ConsentWithdrawalBoundaryConditions改用`perform_full_check`
  - `sec_craft_fixes.py`：`_cached_keys`→`_key_slots`、DegradedReason断言修正、ConsentRecord导入检查修正、HealthCheck检查修正
- 新增`test_sec_supplementary.py`：51条补充测试

### 当前测试总数：388条（目标≥1000条，缺口612条）

### 上次运行残留失败项（17条，需验证是否已修复）
1. `TestEmergencySigningBoundary::test_daily_limit_boundary_at_50` — `_daily_count`属性名错误
2. `TestHSMKeySpecConsistency::test_softhsm_expiry_days` — 导入路径错误
3. `TestSECPerformanceBenchmarks::test_hsm_sign_p99_under_5ms` — SoftHSM延迟17ms
4. `TestTokenFamilyFullChain`（3条）— `rotate_refresh_token`不存在（已修复）
5. `TestSoftHSMExpiryScenarios::test_softhsm_critical_days_constant` — 导入路径错误
6. `TestHSMPaddingFix`（2条）— `_cached_keys`不存在（已修复）
7. `TestHSMDegradedSigningBehavior`（2条）— 正则大小写不匹配（已修复）
8. `TestConsentAPIPermissionControl`（2条）— RoleChecker检查逻辑错误（已修复）
9. `TestCircuitBreakerThreadSafety`（1条）— async方法同步调用（已修复）
10. `TestConsentWithdrawalBoundaryConditions`（3条）— `_check_consent_phase`签名不匹配（已修复）

## 二、审查目标

1. **验证所有已修复项**：运行完整SEC测试套件，确认17条残留失败已全部修复
2. **补充测试至≥1000条**：当前388条，需新增≥612条
3. **性能指标验证**：签名P99<5ms（生产HSM）/50ms（SoftHSM）、撤回后<500ms生效
4. **深度审查未覆盖领域**：时序攻击、密钥提取防护、SQL注入、审计日志篡改、SSRF
5. **文档一致性最终确认**：对照5份核心参考文档完成C-01~C-10检查

## 三、实施步骤

### 阶段1：回归验证（确认已修复项）

**步骤1.1**：运行完整SEC测试套件
```bash
python -m pytest tests/unit/test_hsm* tests/unit/test_desensitization* tests/unit/test_refresh_token* tests/unit/test_consent* tests/unit/test_auth* tests/unit/test_emergency* tests/unit/test_sec* tests/unit/test_security* -v --tb=short -q
```

**步骤1.2**：运行性能基准测试
```bash
python -m pytest tests/benchmark/ -v --tb=short
```

**步骤1.3**：记录通过/失败/错误数量，分类分析失败原因

**验证标准**：所有17条已知失败项应通过；总通过率应>95%

### 阶段2：深度代码审查（补充检查项）

**步骤2.1**：审查`database.py`三表主键与规格不一致问题
- `consent_records`：规格PK=`record_id`，实现PK=`id`
- `safety_events`：规格PK=`event_id`，实现PK=`id`
- `audit_logs`：规格PK=`log_id`(BIGSERIAL)，实现PK=`id`(log_id为String(36) UUID)
- **决策**：评估是否需要迁移（风险高），或更新规格文档（UUID更安全）

**步骤2.2**：审查`consent_repository.py`的`uuid.uuid4().int`溢出风险
- UUID4的int值为128位无符号整数，可能超出PostgreSQL BigInteger（64位有符号）范围
- **修复方案**：改用`uuid.uuid4().int % (2**63)`或直接使用UUID字符串

**步骤2.3**：审查`consent_repository.py` Redis广播全部失败后缺少主动通知
- 当前仅写SafetyEvent，缺少WebSocket推送或告警
- **修复方案**：添加`logger.critical` + 结构化告警

**步骤2.4**：审查脱敏规则覆盖范围不对称
- 主规则有IP地址但SG之前无（已修复sg_ip_address）
- SG有护照号/医保号/车牌号/银行卡号但主规则无
- **修复方案**：在主规则中补充这4类PII

**步骤2.5**：审查`verify_retention_compliance`住院30年检查
- 已添加`overdue_inpatient_archive_purge`检查项
- **验证**：确认`data_retention_inpatient_years`配置项存在且≥30

### 阶段3：大规模测试补充（388→≥1000条）

需要新增≥612条测试。按以下维度分配：

#### 3.1 脱敏引擎深度测试（+150条）
- **参数化PII组合测试**：每种PII类型×5种上下文=50条
- **嵌套结构脱敏**：dict/list/混合嵌套=20条
- **边界条件扩展**：超长文本(10万字符)、纯数字文本、全角字符、零宽字符=20条
- **SafetyGateway异构验证**：每条SG规则×3种输入=30条
- **三层集成端到端**：10种临床场景×3层=30条

#### 3.2 HSM密钥管理深度测试（+120条）
- **密钥槽隔离**：4个槽位×5种操作=20条
- **降级状态机完整转换**：NORMAL→CACHED→RESTRICTED→EMERGENCY→恢复=20条
- **密钥轮换全流程**：轮换前/中/后×4种操作=20条
- **并发安全扩展**：4种并发场景×5次重复=20条
- **SoftHSM生命周期**：启用/活跃/即将过期/已过期/严重超期=20条
- **应急签发完整流程**：启用条件/日限制/补验证/审计日志=20条

#### 3.3 Refresh Token深度测试（+100条）
- **Token Family完整链路**：创建→轮换→重放→家族撤销×5种场景=25条
- **并发会话限制**：1~10个并发会话的边界测试=10条
- **过期处理**：刚过期/过期1天/永不过期=10条
- **Redis vs Memory存储一致性**：双存储×5种操作=25条
- **Token清理机制**：过期清理/手动清理/批量清理=15条
- **安全性验证**：Token猜测/暴力破解/侧信道=15条

#### 3.4 患者同意管理深度测试（+80条）
- **同意状态机**：GRANTED→WITHDRAWN→DENIED转换×5种场景=15条
- **撤回传播延迟**：Redis Streams广播→SafetyActionHandler→边缘端=20条
- **在途诊断拦截**：5种在途状态×撤回=15条
- **权限控制**：5种角色×4种操作=20条
- **审计日志完整性**：同意操作→哈希链验证=10条

#### 3.5 审计与合规深度测试（+80条）
- **哈希链完整性**：单链/多链/跨会话/篡改检测=20条
- **per-session锁并发**：10个会话并发写入=10条
- **数据保留策略**：门诊15年/住院30年/过期清理=20条
- **错误码全覆盖**：E1xxx/E2xxx/E3xxx/E4xxx×4=20条
- **配置项验证**：10个关键SEC配置项的默认值和范围=10条

#### 3.6 性能基准扩展测试（+50条）
- **脱敏P99延迟**：5种文本长度×3种复杂度=15条
- **HSM签名P99延迟**：3种密钥类型×3种数据长度=9条
- **Token验证P99延迟**：3种存储×3种负载=9条
- **撤回传播延迟**：5种场景×2种网络条件=10条
- **并发吞吐量**：5种并发级别=7条

#### 3.7 安全专项测试（+50条）
- **时序攻击**：密码验证/HSM签名/Token验证的时间差异=10条
- **密钥提取防护**：序列化/pickle/内存dump/日志泄漏=10条
- **输入验证**：SQL注入/XSS/路径遍历/命令注入=10条
- **审计日志防篡改**：哈希链断裂检测/日志删除检测=10条
- **SSRF防护**：内部URL访问限制=10条

### 阶段4：文档一致性最终确认

**步骤4.1**：对照03-接口规范.md
- 同意接口GET/POST路径、请求/响应格式
- 认证接口路径与响应格式
- consent_records/safety_events表结构
- 错误码E1008/E1010/E4007

**步骤4.2**：对照01-系统架构设计.md
- SafetyGateway集成方式
- 同意状态两阶段校验

**步骤4.3**：对照02-核心流程与知识库.md
- 知识库构建脱敏步骤

**步骤4.4**：对照00a-术语表.md
- 错误码定义一致性

**步骤4.5**：对照04-安全合规.md和00b-统一配置参考手册.md
- 脱敏规则、数据保留策略、认证机制
- 配置项命名与默认值

**步骤4.6**：若发现更优编码实践，更新参考文档而非改代码

### 阶段5：最终验证与报告

**步骤5.1**：运行完整SEC测试套件（应≥1000条）
**步骤5.2**：运行性能基准测试
**步骤5.3**：统计通过率（目标>98%）
**步骤5.4**：输出审查报告

## 四、审查报告模板

### 4.1 审查发现清单
| 编号 | 模块 | 维度 | 严重程度 | 描述 | 状态 |
|------|------|------|---------|------|------|

### 4.2 修复实施记录
| 编号 | 文件 | 变更描述 | 关联发现 |
|------|------|---------|---------|

### 4.3 测试覆盖率报告
| 模块 | 测试文件数 | 测试用例数 | 通过率 |
|------|-----------|-----------|--------|

### 4.4 性能基准报告
| 指标 | 目标 | 实测 | 达标 |
|------|------|------|------|
| HSM签名P99 | <5ms(生产)/<50ms(SoftHSM) | - | - |
| 脱敏P99 | <5ms | - | - |
| 撤回传播延迟 | <500ms | - | - |
| Token验证P99 | <5ms | - | - |

### 4.5 文档更新建议
| 文档 | 条目 | 更新原因 |
|------|------|---------|

### 4.6 剩余风险清单
| 风险 | 影响 | 缓解措施 |
|------|------|---------|

## 五、关键决策点

1. **database.py三表主键**：迁移风险高，建议更新规格文档承认UUID方案更安全
2. **SoftHSM性能阈值**：生产环境5ms，开发/测试环境放宽至50ms
3. **脱敏规则覆盖对齐**：主规则需补充护照号/医保号/车牌号/银行卡号
4. **测试去重**：现有测试存在重复（HSM降级3处、签名填充3处、Token CRUD 3处），新增测试前先整合

## 六、预期产出

1. 所有SEC测试通过率>98%
2. 测试用例总数≥1000条
3. 性能基准全部达标
4. 完整审查报告（发现/修复/风险/建议）
5. 更新后的参考文档（如有更优实践）
