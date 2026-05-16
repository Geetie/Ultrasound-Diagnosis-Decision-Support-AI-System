# SEC安全合规轨道系统性审查计划

## 审查目标

对SEC轨道（Phase 0A: 三层脱敏集成、HSM密钥管理、患者同意管理、Refresh Token轮换）的所有验证环节和测试逻辑进行全面、系统性审查，确保其正确有效性。

### 核心KPI验证目标

| KPI | 目标值 | 当前状态 |
|-----|--------|----------|
| CI测试数量 | ≥1000条 | ✅ 1251条（已收集） |
| HSM签名P99延迟 | <5ms | 待验证（基准测试存在） |
| 撤回后生效延迟 | <500ms | 待验证（基准测试存在） |

---

## 审查范围

### 源代码文件（9个核心文件）

| 文件 | 功能 | 审查重点 |
|------|------|----------|
| `app/security/three_layer_desensitization.py` | 三层脱敏管理器 | 三层逻辑正确性、all_passed语义、降级处理 |
| `app/security/hsm.py` | HSM密钥管理 | 密钥槽路由、降级阶段、SoftHSM过期、Redis持久化 |
| `app/security/refresh_token.py` | Refresh Token轮换 | Token族检测、并发会话限制、Redis/Memory双实现 |
| `app/security/consent_withdrawal_consumer.py` | 同意撤回消费者 | 事件处理、死信队列、边缘端通知 |
| `app/security/desensitization.py` | 脱敏引擎 | 规则匹配、患者姓名令牌化、规则热加载 |
| `app/security/desensitization_restore.py` | 脱敏还原服务 | AES-256-GCM加密、密钥版本、速率限制 |
| `app/security/auth.py` | 认证鉴权 | JWT RS256签名、角色权限、密钥轮换验证 |
| `app/security/emergency_signing.py` | 应急签名 | 双人复核、每日签名限制、24h补验证 |
| `app/api/v1/consent.py` | 同意API端点 | E1008/E1010错误码、撤回事件发布 |

### 测试文件（31个单元测试 + 5个基准测试）

### 核心参考文档

| 优先级 | 文档 | 关键审查点 |
|--------|------|------------|
| P0 | 04-安全合规.md | 脱敏规则、数据保留策略、认证机制、同意管理 |
| P0 | 01-系统架构设计.md | SafetyGateway集成、三道防线、降级策略 |
| P1 | 03-接口规范.md | 错误码体系、同意接口、诊断接口 |
| P1 | 00a-术语表.md | 错误码、状态枚举、术语一致性 |
| P2 | 00b-统一配置参考手册.md | API相关配置项 |

---

## 审查维度与具体检查项

### 一、设计规范审查

#### 1.1 三层脱敏架构一致性

**文档要求（04-安全合规§2）**：
- 第一层：标准化层前置脱敏（数据进入系统时立即脱敏）
- 第二层：融合层输出脱敏校验（防御LLM推理中意外生成的身份信息）
- 第三层：SafetyGateway结构化身份模式拦截（快速正则匹配兜底）
- `all_passed`语义：第1/2层`modified=True`属正常脱敏行为，不影响`all_passed`判定；仅第3层`passed=False`或`degraded_mode`时`all_passed=False`
- SafetyGateway规则异构冗余：`desensitization_rules_safety.yaml`不直接复制`desensitization_rules.yaml`中的正则

**代码审查点**：
- [ ] `ThreeLayerDesensitizationManager.process_full_pipeline()` 三层执行顺序是否正确
- [ ] `all_passed`判定逻辑是否严格对齐文档语义
- [ ] 第3层`consent_status_at_input`是否强制非None（代码已实现ValueError校验）
- [ ] 双重降级场景处理：融合层降级+SG不可达时的输出限制
- [ ] `desensitization_degraded`参数语义是否正确传递（第2层降级状态→第3层）

#### 1.2 HSM密钥管理架构一致性

**文档要求（04-安全合规§2.1/2.1.1）**：
- 密钥槽分离：JWT签名、数据加密、脱敏映射、应急签名使用不同HSM密钥槽
- Compact Profile：SoftHSM 90天过期，USB HSM为默认方案
- 降级阶段：0-5分钟→CACHED，5-30分钟→RESTRICTED，>30分钟→EMERGENCY
- JWT签名密钥轮换：120天，7天并行验证期
- 脱敏映射表密钥轮换：180天（full）/60天（compact）
- HSM连接池（默认10连接）、本地会话密钥缓存（TTL=5分钟）

**代码审查点**：
- [ ] `KeySlot`枚举是否覆盖所有4个槽位（JWT_SIGNING, DATA_ENCRYPTION, DESENSITIZATION_MAPPING, EMERGENCY_SIGNING）
- [ ] `_KEY_SLOT_ROUTING`前缀路由是否正确覆盖所有密钥名
- [ ] `DegradedPhase`阶段阈值是否对齐文档（5分钟/30分钟）
- [ ] SoftHSM过期检测逻辑（90天/120天Critical）
- [ ] JWT密钥自动轮换检查（`_check_jwt_key_rotation`）
- [ ] 应急签发启用条件：降级>5分钟 + RESTRICTED/EMERGENCY阶段 + 双人复核

#### 1.3 患者同意管理架构一致性

**文档要求（04-安全合规§6）**：
- 同意撤回后系统立即降级
- 检查中撤回：Redis Streams广播`ConsentWithdrawn`事件，取消在途诊断
- 输出拦截：融合层输出前直查PostgreSQL，两阶段校验
- SafetyGateway直查PostgreSQL获取同意状态（不使用缓存）
- 撤回后诊断请求返回HTTP 403 + E1010
- 未签署同意返回HTTP 403 + E1008

**代码审查点**：
- [ ] `ConsentWithdrawalConsumer`是否正确消费Redis Streams
- [ ] `SafetyActionHandler`是否正确标记会话状态为withdrawn
- [ ] `DataCleanupHandler`是否正确清理脱敏映射缓存
- [ ] `SchedulingAgentHandler`是否正确抛出NotImplementedError
- [ ] 死信队列处理：处理失败的事件是否正确进入`CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM`
- [ ] 边缘端AI标注停止通知是否正确发布
- [ ] `ConsentChecker`是否严格直查PostgreSQL（不使用Redis缓存）

#### 1.4 Refresh Token轮换架构一致性

**文档要求（04-安全合规§4）**：
- Refresh Token轮换机制
- 单用户最大并发会话数（默认5，`MAX_CONCURRENT_SESSIONS_PER_USER`）
- 会话互踢：第6个会话登录时踢出最早的会话
- JWT有效期8小时，刷新令牌7天

**代码审查点**：
- [ ] Token族（family）检测与撤销逻辑是否正确
- [ ] Token重用检测：已撤销token再次使用时是否撤销整个族
- [ ] 并发会话限制：`_enforce_concurrent_session_limit`是否正确踢出最旧token
- [ ] Memory/Redis双实现是否功能对等
- [ ] 过期token清理逻辑是否正确

### 二、代码质量审查

#### 2.1 健壮性

- [ ] HSM `sign()`方法在SoftHSM过期后是否正确拒绝签名
- [ ] `DesensitizationRestoreService._get_or_create_aes_key()`在Redis不可用时是否正确处理
- [ ] `ConsentWithdrawalConsumer._consume_loop()`异常后是否正确重试
- [ ] `EmergencySigningManager.enable_emergency_signing()`双人复核是否严格验证

#### 2.2 可维护性

- [ ] 单例模式实现是否线程安全（double-checked locking）
- [ ] Redis持久化操作是否统一使用`_safe_run_async_task`
- [ ] 配置项是否通过`settings`统一管理

#### 2.3 性能

- [ ] HSM签名操作是否使用PSS填充（通用签名）vs PKCS1v15（JWT签名）——文档要求RS256 PKCS#1 v1.5填充用于JWT
- [ ] 脱敏还原服务AES密钥缓存TTL是否对齐文档（5分钟）
- [ ] 脱敏映射表加密密钥与JWT签名密钥是否分离（不同HSM密钥槽）

### 三、代码风格审查

- [ ] 日志是否统一使用structlog
- [ ] 类型注解是否完整
- [ ] 异常处理是否遵循项目规范
- [ ] 命名是否与术语表一致

### 四、功能正确性审查

#### 4.1 脱敏规则正确性

**文档要求（04-安全合规§2）**：
- 患者姓名→匿名令牌（张三→PATIENT_001）
- 身份证号→全星号替换
- 手机号→仅保留后4位
- 住址→仅保留到市级
- 出生日期→仅保留年份
- 年龄（整数）→不脱敏

**代码审查点**：
- [ ] 正则表达式是否正确匹配各数据类型
- [ ] 患者姓名令牌化是否确定性（同一姓名→同一令牌）
- [ ] 年龄字段是否正确排除在脱敏之外
- [ ] 身份证号正则是否避免误匹配（前后不能是数字或星号）

#### 4.2 错误码一致性

**文档要求（00a-术语表）**：
- E1008：患者未签署知情同意
- E1010：患者已撤回知情同意
- E4005：SafetyGateway不可用
- E4005A：SafetyGateway模块故障
- E4005B：PostgreSQL不可用
- E4013：HSM不可用

**代码审查点**：
- [ ] `PatientNotConsentedException`是否返回E1008
- [ ] `PatientConsentWithdrawnException`是否返回E1010
- [ ] HSM不可用时是否返回E4013

#### 4.3 边界条件

- [ ] 空数据脱敏处理
- [ ] 无同意记录时的默认行为（应返回E1008/DENIED）
- [ ] HSM降级各阶段行为是否正确
- [ ] Token过期与撤销的边界
- [ ] 脱敏映射表密钥轮换期间的并发读写

### 五、测试逻辑审查

#### 5.1 测试覆盖率

- [ ] 三层脱敏：是否覆盖所有三层独立执行和完整流水线
- [ ] HSM：是否覆盖密钥生成、签名、验证、加密、解密、轮换、降级
- [ ] Refresh Token：是否覆盖创建、验证、轮换、撤销、族撤销、并发限制
- [ ] 同意撤回：是否覆盖撤回事件处理、死信队列、边缘端通知
- [ ] 应急签名：是否覆盖启用条件、双人复核、每日限制

#### 5.2 测试正确性

- [ ] Mock是否正确模拟了外部依赖（Redis、PostgreSQL、HSM）
- [ ] 断言是否验证了关键业务逻辑而非仅验证返回值类型
- [ ] 异步测试是否正确使用`@pytest.mark.asyncio`
- [ ] 基准测试的warmup是否充分

#### 5.3 边界条件测试

- [ ] SoftHSM过期边界（89天/90天/91天）
- [ ] HSM降级阶段边界（4:59/5:00/5:01分钟）
- [ ] 并发会话数边界（4/5/6个会话）
- [ ] Token族重用检测
- [ ] 空数据/None值处理

### 六、性能基准测试审查

#### 6.1 HSM签名P99<5ms

- [ ] 基准测试`test_hsm_signing_performance.py`是否正确测量
- [ ] 1000次采样是否统计显著
- [ ] warmup迭代是否充分（当前10次）
- [ ] 不同payload大小的性能是否都在SLA内

#### 6.2 撤回后生效<500ms

- [ ] 基准测试`test_revocation_latency_benchmark.py`是否正确测量端到端延迟
- [ ] SafetyActionHandler处理延迟P99是否<500ms
- [ ] 并发撤回场景是否测试

---

## 已发现的初步问题

### 问题1：HSM sign()方法使用PSS填充，但JWT应使用PKCS1v15

**位置**：`app/security/hsm.py` L495-502
**描述**：`sign()`方法对所有非emergency签名使用PSS填充，但文档（04-安全合规§4）明确要求JWT RS256使用PKCS#1 v1.5填充。虽然`sign_jwt()`方法（L650-654）正确使用了PKCS1v15，但`sign()`方法的通用签名应使用RSASSA-PSS（SHA-256），这与文档一致。**当前实现实际正确**——`sign()`用于通用数据签名（PSS），`sign_jwt()`用于JWT签名（PKCS1v15），对齐文档要求。

### 问题2：脱敏映射表密钥轮换周期未实现

**位置**：`app/security/desensitization_restore.py`
**描述**：文档要求脱敏映射表加密密钥每180天轮换（compact 60天），但代码中`re_encrypt_with_new_key()`方法存在但无自动轮换调度。`_check_jwt_key_rotation()`仅检查JWT密钥，未覆盖脱敏映射密钥。

### 问题3：HSM连接池未实现

**位置**：`app/security/hsm.py`
**描述**：文档要求"HSM连接池（默认10连接）"，但当前HSMManager为单实例，无连接池概念。在SoftHSM模式下，所有操作通过内存中的密钥对象执行，连接池不适用；但在真实HSM硬件部署时需要。

### 问题4：本地会话密钥缓存TTL对齐

**位置**：`app/security/desensitization_restore.py` L17
**描述**：`_KEY_CACHE_TTL = timedelta(minutes=5)` 与文档"本地会话密钥缓存（TTL=5分钟）"一致。✅

### 问题5：SchedulingAgentHandler未实现

**位置**：`app/security/consent_withdrawal_consumer.py` L134-143
**描述**：`SchedulingAgentHandler.handle_withdrawal()`抛出`NotImplementedError`，文档要求"调度智能体收到后取消该会话所有在途诊断任务"。此为集成依赖项，需在AGT轨道完成后实现。

### 问题6：基准测试使用模拟I/O

**位置**：`tests/benchmark/sec/test_hsm_signing_performance.py`、`test_revocation_latency_benchmark.py`
**描述**：基准测试在模拟I/O环境下运行（HSM操作为内存模拟，Redis/DB为内存模拟），测量结果仅反映逻辑正确性和相对性能，不代表真实硬件的绝对延迟。文档明确要求"一期开发阶段须在模拟生产负载下验证P99<10ms达标"。

### 问题7：脱敏引擎患者姓名令牌化HMAC密钥初始化

**位置**：`app/security/desensitization.py` L48
**描述**：`_name_hmac_key = os.urandom(32)` 在首次初始化时生成随机密钥，后续从Redis加载。但若Redis不可用且进程重启，将生成新密钥导致令牌映射不一致。虽有`load_state_from_redis()`方法尝试恢复，但初始化顺序存在竞态风险。

---

## 审查执行结果

### 设计规范审查结果

#### 三层脱敏架构一致性 ✅ 基本通过

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 三层执行顺序（标准化→融合→SG） | ✅ 通过 | `process_full_pipeline()` 顺序正确 |
| `all_passed`语义对齐文档 | ✅ 通过 | 第1/2层`modified`不影响`all_passed`，仅第3层`passed=False`或`degraded_mode`时为False |
| `consent_status_at_input`强制非None | ✅ 通过 | L141 ValueError校验 |
| 双重降级场景处理 | ✅ 通过 | `desensitization_degraded`参数正确传递第2层降级状态至第3层 |
| SafetyGateway规则异构冗余 | ✅ 通过 | `desensitization_rules_safety.yaml`与`desensitization_rules.yaml`规则独立编写 |
| AES-256-GCM加密+HSM密钥封装 | ✅ 通过 | `desensitization_restore.py`正确实现 |
| key_version追踪 | ⚠️ 小缺陷 | `re_encrypt_with_new_key()` L342应使用`actual_version`而非`new_key_version` |
| 速率限制区分compact/full | ❌ 不通过 | 未按部署规格区分100/hr(compact) vs 200/hr(full) |

#### HSM密钥管理架构一致性 ✅ 基本通过

| 检查项 | 状态 | 说明 |
|--------|------|------|
| KeySlot 4槽位覆盖 | ✅ 通过 | JWT_SIGNING/DATA_ENCRYPTION/DESENSITIZATION_MAPPING/EMERGENCY_SIGNING |
| _KEY_SLOT_ROUTING前缀路由 | ✅ 通过 | jwt_/desens_/emergency_ 前缀正确路由 |
| DegradedPhase阶段阈值 | ✅ 通过 | 配置项`hsm_degraded_phase_1/2_threshold_minutes`可配置 |
| SoftHSM 90天过期/120天Critical | ✅ 通过 | `_check_soft_hsm_expiry()` + `_start_soft_hsm_periodic_check()` |
| JWT签名PKCS1v15填充 | ✅ 通过 | `sign_jwt()` L650-654 使用PKCS1v15 |
| 通用签名PSS填充 | ✅ 通过 | `sign()` L495-502 使用PSS |
| 应急签名双人复核 | ✅ 通过 | `enable_emergency_signing()` L58-61 严格验证 |
| 应急签名5分钟降级条件 | ✅ 通过 | L75 检查`degraded_duration < timedelta(minutes=5)` |
| JWT密钥7天并行验证期 | ✅ 通过 | `rotate_key()` L660 `parallel_days=7` |
| Redis密钥恢复 | ❌ **Critical** | `_restore_keys_from_redis()` 前缀剥离Bug，服务重启后密钥丢失 |
| 脱敏映射密钥自动轮换 | ❌ 不通过 | `re_encrypt_with_new_key()`存在但无自动调度 |
| HSM连接池 | ⚠️ 缺失 | 文档要求10连接池，SoftHSM模式不适用但硬件HSM需预留 |

#### 患者同意管理架构一致性 ⚠️ 部分通过

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Redis Streams撤回事件发布 | ✅ 通过 | `consent.py` L116 `xadd` |
| ConsentWithdrawalConsumer消费 | ✅ 通过 | `_consume_loop()` 正确实现 |
| SafetyActionHandler会话标记 | ✅ 通过 | 标记`consent_status_at_creation="withdrawn"` |
| DataCleanupHandler缓存清除 | ❌ **High** | scan模式缺少`ultrasound`前缀，缓存无法清除 |
| SchedulingAgentHandler | ⚠️ 未实现 | 抛出NotImplementedError，依赖AGT轨道 |
| 死信队列 | ✅ 通过 | `_handle_processing_error()` 正确写入dead letter stream |
| 边缘端AI标注停止 | ✅ 通过 | `edge_stop_ai_annotation` 发布 |
| ConsentChecker直查PG | ✅ 通过 | 严格直查PostgreSQL，不使用Redis缓存 |
| E1008/E1010错误码 | ✅ 通过 | `PatientNotConsentedException`/`PatientConsentWithdrawnException` |

#### Refresh Token轮换架构一致性 ⚠️ 部分通过

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Token族检测与撤销 | ✅ 通过 | `revoke_token_family()` 正确实现 |
| Token重用检测 | ✅ 通过 | 已撤销token重用→撤销整个族 |
| 并发会话限制 | ✅ 通过 | `_enforce_concurrent_session_limit()` |
| Memory/Redis功能对等 | ✅ 通过 | 两种实现功能一致 |
| Auth API Redis模式兼容 | ❌ **High** | `auth.py` L138/141-142 同步调用异步方法，Redis模式崩溃 |
| 过期token清理 | ✅ 通过 | `cleanup_expired_tokens()` |

---

## 发现的BUG清单（按严重程度排序）

### 🔴 Critical-1: HSM Redis密钥恢复完全失效

**文件**: [hsm.py:964](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L964)
**影响**: 服务重启后所有HSM密钥从Redis恢复失败，导致JWT签名失败、数据解密失败
**根因**: `_restore_keys_from_redis()` 中 `key_str.replace(":hsm:keys:", "")` 未剥离Redis键前缀`ultrasound`。实际键名为`ultrasound:hsm:keys:{slot}:{key_name}:private`，替换后得到`ultrasound:{slot}:{key_name}`，slot前缀检测`startswith("jwt_signing:")`因`ultrasound:`前缀而失败
**修复方案**: 替换为 `raw_key_name = key_str.replace(f"{_PREFIX}:hsm:keys:", "").replace(private_suffix, "")` 或使用 `RedisKey.hsm_key()` 的反向解析

### 🔴 High-1: DataCleanupHandler脱敏缓存清除失败

**文件**: [consent_withdrawal_consumer.py:155](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/consent_withdrawal_consumer.py#L155)
**影响**: 患者撤回同意后脱敏映射缓存无法清除，违反数据清理合规要求
**根因**: `scan_iter(f":desens:session:*:{event.patient_id}")` 缺少前缀`ultrasound`，实际键名为`ultrasound:desens:session:*:{patient_id}`
**修复方案**: 使用 `RedisKey.desens_session("*", event.patient_id)` 或 `f"{_PREFIX}:desens:session:*:{event.patient_id}"`

### 🔴 High-2: Auth API Redis存储模式登录崩溃

**文件**: [auth.py:138,141-142](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/api/v1/auth.py#L138)
**影响**: `USE_REDIS_STORAGE=true`时，`/auth/token`和`/auth/login`端点崩溃
**根因**: `refresh_token_store.get_user_token_count()` 和 `revoke_oldest_user_token()` 在Redis模式下为async方法，但被同步调用（缺少`await`）
**修复方案**: 添加`await`并使用`refresh_token.py`中的模块级异步函数

### 🟡 Medium-1: 脱敏还原速率限制未区分compact/full

**文件**: [desensitization_restore.py:152-169](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/desensitization_restore.py#L152)
**影响**: 未按部署规格区分100/hr(compact) vs 200/hr(full)速率限制
**根因**: `_check_rate_limit()` 使用统一的`settings.desensitization_restore_rate_limit`，未根据部署profile调整
**修复方案**: 根据部署profile（compact/full）设置不同的默认值

### 🟡 Medium-2: re_encrypt_with_new_key密钥版本不一致

**文件**: [desensitization_restore.py:342](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/desensitization_restore.py#L342)
**影响**: 重加密时`key_version`可能不正确
**根因**: L342使用参数`new_key_version`而非`_encrypt_value()`返回的`actual_version`
**修复方案**: `mapping.key_version = actual_version`

### 🟡 Medium-3: 脱敏映射表密钥无自动轮换调度

**文件**: [desensitization_restore.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/desensitization_restore.py)
**影响**: 文档要求180天(full)/60天(compact)轮换，但无自动触发机制
**根因**: `_check_jwt_key_rotation()`仅覆盖JWT密钥，脱敏映射密钥无对应检查
**修复方案**: 在`_start_key_cleanup_timer`中增加脱敏映射密钥轮换检查

### 🟢 Low-1: 脱敏引擎HMAC密钥初始化竞态

**文件**: [desensitization.py:48](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/desensitization.py#L48)
**影响**: Redis不可用且进程重启时，HMAC密钥重新生成导致令牌映射不一致
**根因**: `_name_hmac_key = os.urandom(32)` 在`__new__`中初始化，`load_state_from_redis()`异步加载存在时序窗口

### 🟢 Low-2: SchedulingAgentHandler未实现

**文件**: [consent_withdrawal_consumer.py:134-143](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/consent_withdrawal_consumer.py#L134)
**影响**: 调度智能体撤回处理未实现，在途诊断任务无法取消
**根因**: 依赖AGT轨道完成

---

## 测试审查结果

### CI测试数量 ✅ 达标

- **已收集**: 1251条测试（目标≥1000）
- **测试文件**: 31个单元测试 + 5个基准测试

### 测试覆盖缺口 ❌ 需补充

| 缺口领域 | 缺失测试 | 优先级 |
|----------|----------|--------|
| 三层脱敏管道 | `process_full_pipeline()`完整流程测试 | High |
| `all_passed`语义 | 第1/2层modified不影响all_passed的断言测试 | High |
| 双重降级场景 | 融合层降级+SG不可达的集成测试 | High |
| HSM Redis恢复 | `_restore_keys_from_redis()`带前缀的端到端测试 | Critical |
| DataCleanupHandler | 带正确前缀的scan_iter测试 | High |
| Auth Redis模式 | `USE_REDIS_STORAGE=true`下的登录端点测试 | High |
| 脱敏映射密钥轮换 | `re_encrypt_with_new_key()`并发读写测试 | Medium |
| 速率限制compact/full | 不同部署规格的速率限制测试 | Medium |

### 性能基准测试 ✅ 框架就绪

| 基准 | 测试文件 | SLA | 状态 |
|------|----------|-----|------|
| HSM签名P99 | `test_hsm_signing_performance.py` | <5ms | 框架就绪，模拟I/O |
| JWT签名P99 | `test_hsm_signing_performance.py` | <5ms | 框架就绪，模拟I/O |
| 撤回处理P99 | `test_revocation_latency_benchmark.py` | <500ms | 框架就绪，模拟I/O |
| 签名吞吐量 | `test_hsm_signing_performance.py` | >100ops/s | 框架就绪 |

**注意**: 所有基准测试在模拟I/O环境下运行，不代表真实硬件的绝对延迟。需在集成测试环境中使用真实Redis/PostgreSQL验证。

---

## 改进建议（最终版）

### Critical（必须修复）

1. **修复HSM Redis密钥恢复前缀剥离Bug** — `_restore_keys_from_redis()` 中正确剥离`_PREFIX`前缀
2. **修复DataCleanupHandler scan模式前缀** — 使用`RedisKey.desens_session()`或添加`_PREFIX`前缀
3. **修复Auth API Redis模式await缺失** — 同步调用改为`await`异步调用

### High（强烈建议修复）

4. **补充三层脱敏管道集成测试** — `process_full_pipeline()`、`all_passed`语义、双重降级
5. **补充HSM Redis恢复端到端测试** — 验证带前缀的键名正确恢复
6. **实现脱敏映射表密钥自动轮换调度** — 对齐文档180天/60天周期
7. **补充Auth Redis模式端到端测试** — `USE_REDIS_STORAGE=true`下的登录流程

### Medium（建议修复）

8. **脱敏还原速率限制区分compact/full** — 根据部署profile设置不同默认值
9. **修复re_encrypt_with_new_key密钥版本** — 使用`actual_version`替代`new_key_version`
10. **基准测试补充真实I/O场景** — 增加集成测试级别的性能验证

### Low（可选优化）

11. **增强HMAC密钥持久化健壮性** — Redis不可用时的密钥恢复策略
12. **HSM连接池接口预留** — 为硬件HSM部署预留接口
13. **测试套件整合** — 合并功能重叠的测试文件
