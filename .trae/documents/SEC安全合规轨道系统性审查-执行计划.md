# SEC安全合规轨道系统性审查 — 执行计划

## 审查完成状态

| 步骤 | 维度 | 状态 | 发现数 |
|------|------|------|--------|
| 1 | 设计规范一致性 | ✅ 完成 | 20项检查，5 PASS / 10 PARTIAL / 5 FAIL |
| 2 | 代码质量 | ✅ 完成 | 16项检查，2 PASS / 4 PARTIAL / 10 FAIL |
| 3 | 功能正确性 | ✅ 完成 | 16项检查，1 PASS / 6 PARTIAL / 9 FAIL |
| 4 | 测试逻辑 | ✅ 完成 | 16项检查，3 PASS / 10 PARTIAL / 3 FAIL |
| 5 | 代码风格 | ✅ 完成 | 5项检查，0 PASS / 2 PARTIAL / 3 FAIL |
| **合计** | | | **73项检查，11 PASS / 32 PARTIAL / 30 FAIL** |

---

## 一、Critical级发现（必须立即修复，影响安全合规底线）

### C1. 脱敏-还原映射表完全未实现
- **检查项**: D1.1.5
- **现状**: 无 `DesensitizationRestoreService`、无映射表数据结构、无 AES-256-GCM 加密、无 180 天轮换、无密钥分离
- **影响**: 规格要求脱敏后的数据可通过授权还原，映射表是核心安全组件，完全缺失意味着脱敏不可逆
- **修复方案**: 
  1. 实现 `DesensitizationRestoreService`，AES-256-GCM 加密映射记录
  2. HSM 中新增 `mapping_encryption_key` 密钥槽，与 JWT 签名密钥分离
  3. 添加 `key_version` 字段，180 天自动轮换定时任务
  4. 每次访问记录审计日志

### C2. HSM 应急签发方案完全未实现
- **检查项**: D1.2.6
- **现状**: RESTRICTED/EMERGENCY 阶段直接禁止所有签名，无管理员手动启用机制、无审计日志、无 24 小时追溯验证
- **影响**: HSM 不可用超过 5 分钟时系统完全瘫痪，无法签发任何报告，违反业务连续性要求
- **修复方案**:
  1. 实现 `enable_emergency_signing(admin_user, reason)` 方法，需双人复核
  2. 应急签发的报告标记 `pending_verification: true`
  3. HSM 恢复后 24 小时内执行补签名验证
  4. 所有应急操作写入审计日志

### C3. 同意撤回事件消费侧完全缺失
- **检查项**: D1.3.2
- **现状**: Redis Streams 广播 `ConsentWithdrawn` 事件已实现，但调度智能体未实现事件消费，在途诊断任务无法取消，边缘端未停止 AI 标注
- **影响**: 患者撤回同意后，已提交的诊断任务可能继续执行并输出结果，直接违反知情同意法律要求
- **修复方案**:
  1. 实现调度智能体订阅 `consent:withdrawal` Redis Stream
  2. 收到撤回事件后取消该患者所有在途诊断任务
  3. 已产生的中间结果不输出、不缓存
  4. 通知边缘端停止 AI 标注

### C4. 调度层硬编码 `consent_status_at_input="granted"`
- **检查项**: D1.3.1
- **现状**: `diagnosis.py` 调用 SafetyGateway 时硬编码同意状态为 "granted"，导致 SafetyGateway 入口快速拒绝逻辑形同虚设
- **影响**: 即使患者已撤回同意，SafetyGateway 的入口快速拒绝也不会触发
- **修复方案**:
  1. 调度层将实际同意检查结果注入请求上下文
  2. 传递真实 `consent_status_at_input` 给 SafetyGateway
  3. 添加 500ms TTL 缓存层

### C5. Redis Refresh Token Store 重用检测缺失
- **检查项**: D1.4.1 / F4.4.1
- **现状**: `RedisRefreshTokenStore.verify_refresh_token` 在 token 不存在时仅记录日志，不检查 `token_family:{token_id}` 映射，无法检测已消费 token 的重用
- **影响**: 攻击者重放已消费的 refresh token 不会触发家族撤销，防盗用机制失效
- **修复方案**: 在 `verify_refresh_token` 中添加 `token_family:{token_id}` 映射查询，检测到映射存在时触发 `revoke_token_family`

### C6. 审计哈希链并发断裂
- **检查项**: Q2.1.7
- **现状**: `log_action` 中 `_get_last_hash` 和 `db.add` 之间存在 TOCTOU 窗口，并发写入导致哈希链分叉断裂
- **影响**: 审计日志完整性被破坏，`verify_chain` 可检测但无法修复
- **修复方案**:
  1. 添加 `asyncio.Lock` 串行化 `log_action` 操作
  2. 或使用 `SELECT ... FOR UPDATE` 加数据库级锁
  3. 或使用 PostgreSQL advisory lock

### C7. HSM `_safe_run_async_task` fire-and-forget 风险
- **检查项**: Q2.1.1
- **现状**: 异步任务创建后返回值未被保存，`_pending_tasks` WeakSet 声明但从未使用，任务可能被 GC 回收导致静默丢失
- **影响**: 密钥持久化/恢复操作可能静默失败，重启后密钥丢失
- **修复方案**:
  1. 将 `create_task` 返回的 task 对象添加到 `_pending_tasks`
  2. 添加任务完成回调，记录成功/失败日志
  3. 关键操作（密钥持久化、降级状态持久化）改为同步等待完成

### C8. HSM 降级状态 Redis TTL 仅 24 小时
- **检查项**: Q2.1.3
- **现状**: EMERGENCY 阶段（安全关键状态）的 Redis TTL 仅 24 小时，无续期机制，过期后重启将错误恢复到 NORMAL
- **影响**: HSM 降级状态丢失后，签名能力被错误恢复，违反安全策略
- **修复方案**:
  1. 将 TTL 改为与降级生命周期匹配（如 7 天）
  2. 添加定时续期机制（每 12 小时刷新 TTL）
  3. 三个 key 使用 pipeline 原子设置，确保 TTL 一致

---

## 二、High级发现（影响系统安全性和可靠性）

### H1. HSM 密钥分离未实现
- **检查项**: D1.2.5
- **现状**: 无映射表加密密钥定义，无 HSM 密钥槽概念，所有密钥存储在同一个 `_cached_keys` 字典
- **修复**: 新增密钥槽抽象，JWT 签名密钥和映射表加密密钥使用不同槽位

### H2. HSM 四级降级行为不完整
- **检查项**: D1.2.1
- **现状**: 枚举和阈值正确，但 CACHED 阶段无差异化行为、RESTRICTED 不区分新旧会话、EMERGENCY 无运维响应
- **修复**: 各阶段实现规格要求的具体行为（告警、暂停新会话、运维响应）

### H3. SoftHSM 90 天过期告警不完整
- **检查项**: D1.2.2
- **现状**: 启动检查和签名拒绝已实现，但缺少管理界面不可关闭警告、缺少每周周期性告警、时间戳文件可被篡改
- **修复**: 添加周期性告警定时任务，时间戳文件添加 HMAC 完整性校验

### H4. 密钥轮换旧密钥清理无自动调度
- **检查项**: D1.2.3
- **现状**: `cleanup_expired_keys()` 存在但从未被自动调用，Redis 中旧密钥未清理
- **修复**: 添加后台定时任务调用 `cleanup_expired_keys()`，同步清理 Redis

### H5. 同意撤回广播失败无补偿机制
- **检查项**: Q2.1.5 / F4.3.2
- **现状**: 3 次重试失败后仅写 SafetyEvent，无重试队列、无延迟重发、`db.add()` 未 flush
- **修复**: 添加本地持久化重试队列，设置定时器周期性重试，`db.add()` 后添加 `flush()`

### H6. SafetyGateway checker 单例非线程安全
- **检查项**: Q2.1.6
- **现状**: `get_safety_gateway_checker()` 无双重检查锁，并发调用可能创建多个实例
- **修复**: 添加 `threading.Lock` + 双重检查锁定模式

### H7. 同意查询 API 非特权角色无权限控制
- **检查项**: F4.3.1
- **现状**: `get_consent` 端点检测到非特权角色后仅 `pass`，任何已认证用户可查询任意患者同意状态
- **修复**: 添加 `RoleChecker` 依赖或抛出 403 异常

### H8. SafetyGateway 熔断器降级逻辑与文档矛盾
- **检查项**: F4.3.3
- **现状**: 熔断器打开且 Redis 缓存未命中时，绕过熔断器直接查 PostgreSQL，与文档"拦截所有输出"矛盾
- **修复**: 缓存未命中时直接返回 `ConsentCheckResult(None, False, True)`，拦截所有输出

### H9. Redis 后端家族撤销非原子
- **检查项**: F4.4.3
- **现状**: `revoke_token_family` 逐个执行 SET/SREM，进程中途崩溃将导致家族撤销不完整
- **修复**: 使用 `pipeline(transaction=True)` 批量执行所有 SET 和 SREM

### H10. `_get_encryption_key` 环境变量缺失时服务仍可启动
- **检查项**: F4.2.3
- **现状**: 异常在异步调用链中被静默吞掉，服务可在无加密密钥时启动
- **修复**: 在 `__init__` 中同步检查 `HSM_KEY_ENCRYPTION_KEY` 环境变量

### H11. IP 地址正则误匹配版本号 + study_instance_uid 锚定漏匹配
- **检查项**: F4.1.2 / F4.1.4
- **现状**: IP 正则无边界约束匹配版本号；`study_instance_uid` 使用 `^...$` 锚定导致中间文本漏匹配，子串反被 IP 正则误脱敏
- **修复**: IP 正则添加前后向断言；`study_instance_uid`/`hospital_id` 移除 `^$` 锚定改用 `\b` 或前后向断言

### H12. HSM Redis 持久化恢复时序问题
- **检查项**: Q2.1.2
- **现状**: `_restore_keys_from_redis` 在 `__init__` 中 fire-and-forget 调用，恢复未完成时 `_cached_keys` 为空，签名操作将失败
- **修复**: 添加同步屏障确保恢复完成后再提供服务，或在首次签名操作时检查恢复状态

---

## 三、Medium级发现（影响可维护性和代码质量）

### M1. 异常处理体系三套并行
- **检查项**: Q2.2.4 / S3.4
- **现状**: HSM 用 RuntimeError，Consent 用 AppBaseException(HTTPException)，SafetyGateway 用 SafetyGatewayError(Exception)
- **修复**: HSM 定义 `HSMError` 基类替代 RuntimeError；auth.py 使用 AppBaseException 替代裸 HTTPException

### M2. Refresh Token 模块 isinstance 违反开闭原则
- **检查项**: Q2.2.2
- **现状**: 8 处 isinstance 分支判断后端类型，根因是同步/异步接口不统一
- **修复**: 基类方法全部改为 async，消除 isinstance 分支

### M3. 三层脱敏管理器非单例
- **检查项**: Q2.2.3
- **现状**: `get_three_layer_desensitization_manager()` 每次返回新实例
- **修复**: 采用与 MemoryRefreshTokenStore 一致的单例模式

### M4. HSM 密钥元数据 Redis TTL 365 天
- **检查项**: Q2.2.1
- **现状**: 密钥轮换后旧元数据在 Redis 中残留近一年
- **修复**: Redis TTL 改为与密钥 `expires_at` 对齐，`cleanup_expired_keys` 同步清理 Redis

### M5. 诊断请求 API 错误码未区分
- **检查项**: D1.3.5
- **现状**: 已撤回患者返回 E1008（未签署）而非 E1010（已撤回）
- **修复**: `diagnosis.py` 中区分 `ConsentStatus.WITHDRAWN` 和其他非 GRANTED 状态

### M6. `all_passed` 判断逻辑未覆盖 sg_unavailable/failed 状态
- **检查项**: D1.1.4
- **现状**: `status != "degraded"` 对 `sg_unavailable` 和 `failed` 状态返回 True，可能导致误判
- **修复**: 改为 `status == "passed"` 正向判断

### M7. 嵌套字典递归无深度限制
- **检查项**: F4.1.6
- **现状**: `desensitize_dict` 无递归深度限制，同项目 safety_gateway 已实现 MAX_RECURSION_DEPTH=10
- **修复**: 添加 `_depth` 参数和 MAX_RECURSION_DEPTH=20 限制

### M8. HSM 降级事件去重逻辑过于激进
- **检查项**: F4.2.4
- **现状**: 只要存在任何历史 HSM_DEGRADED 事件就跳过，降级-恢复-再降级场景漏记
- **修复**: 基于当前降级开始时间 `_degraded_at` 进行去重

### M9. Redis 后端并发会话限制存在 TOCTOU 竞态
- **检查项**: F4.4.2
- **现状**: 检查计数和创建 token 非原子，并发请求可能突破限制
- **修复**: 将并发限制逻辑内聚到 `RedisRefreshTokenStore.create_refresh_token` 内部，使用 Redis Lua 脚本保证原子性

### M10. `/token` 和 `/login` 端点行为不一致
- **检查项**: F4.4.4
- **现状**: `/token` 不签发 refresh token，`/login` 签发
- **修复**: 统一两个端点的 refresh token 签发行为

### M11. 配置命名与参考手册不匹配
- **检查项**: S3.5
- **现状**: 3 个核心配置项名称不匹配，9 个安全配置项在 settings.py 中缺失，hsm.py 绕过统一配置管理
- **修复**: 对齐配置项命名，将 hsm.py 的环境变量纳入 settings.py 统一管理

### M12. docstring 覆盖率严重不均
- **检查项**: S3.1
- **现状**: three_layer_desensitization.py 完整，hsm.py/refresh_token.py 近乎为零
- **修复**: 为 hsm.py 和 refresh_token.py 的公开方法添加 docstring

### M13. 命名约定不一致
- **检查项**: S3.2
- **现状**: 布尔属性 `is_` 前缀不一致，数据存储属性命名角度不统一
- **修复**: 统一布尔属性命名（`_is_xxx`），统一数据存储命名（`_xxx_store`）

---

## 四、Low级发现（改进建议）

### L1. patient_name_simple 正则过度匹配
- **检查项**: F4.1.3
- **现状**: `^` 锚定提供部分保护，但医疗报告字段仍高频误判
- **修复**: 增加负向前向断言排除常见医疗非姓名词汇

### L2. set 类型脱敏处理不一致
- **检查项**: F4.1.5
- **现状**: set 中非 str 元素被强制 `str()` 转换，与 list/tuple 处理不一致
- **修复**: set 中非 str 元素原样保留

### L3. 日志级别选择不一致
- **检查项**: S3.3
- **现状**: 同等严重安全事件在不同模块使用 error/warning 不同级别
- **修复**: 制定安全事件日志级别统一约定

### L4. 性能基准测试条件不完善
- **检查项**: T5.2.1
- **现状**: 撤销/脱敏基准缺少预热轮次，日志基准未禁用 GC
- **修复**: 添加 50 次预热轮次，统一 GC 禁用

### L5. 测试计数未达 CI≥1000
- **检查项**: T5.1.1
- **现状**: SEC 相关测试约 681 个，距目标差约 320 个
- **修复**: 重点补充 RedisRefreshTokenStore、HSM Redis 持久化、同意 E2E 场景测试

### L6. 永真断言未删除
- **检查项**: T5.1.3
- **现状**: TestConsentManagement 中 2 个永真断言仍存在
- **修复**: 删除永真测试，保留 test_k1_fixes.py 中的正确替代

### L7. 全局 conftest.py 未统一重置 SEC 单例
- **检查项**: T5.1.4
- **现状**: 各测试文件自行处理隔离，全局 conftest.py 仅重置脱敏引擎
- **修复**: 在 conftest.py 中统一添加 HSM/TokenStore/SG Checker 重置

### L8. RedisRefreshTokenStore 无功能测试
- **检查项**: T5.3.4
- **现状**: 所有 refresh_token 测试使用 MemoryRefreshTokenStore
- **修复**: 使用 fakeredis 补全 RedisRefreshTokenStore 所有方法测试

### L9. 脱敏映射表加密无测试
- **检查项**: T5.3.6
- **现状**: 映射关系存储安全性无测试覆盖
- **修复**: 实现 C1 后添加对应测试

### L10. SafetyGateway 性能基准过度依赖 Mock
- **检查项**: Q2.3.5
- **现状**: Consent 检查被 Mock，吞吐量数据不反映真实场景
- **修复**: 添加含真实 DB/Redis 依赖的集成性能测试

---

## 五、执行步骤

### 阶段1：Critical 修复（8项，优先级最高）

| 序号 | 发现 | 修复文件 | 修复内容 |
|------|------|---------|---------|
| 1 | C1 映射表未实现 | 新增 `app/security/desensitization_restore.py` | 实现 DesensitizationRestoreService + AES-256-GCM + HSM 密钥槽 |
| 2 | C2 应急签发未实现 | `app/security/hsm.py` | 实现 enable_emergency_signing + 补签名验证 |
| 3 | C3 撤回消费侧缺失 | 新增 `app/services/consent_withdrawal_consumer.py` | 调度智能体订阅 Redis Stream + 取消在途任务 |
| 4 | C4 硬编码同意状态 | `app/api/v1/diagnosis.py` | 传递真实 consent_status_at_input |
| 5 | C5 Redis 重用检测 | `app/security/refresh_token.py` | verify_refresh_token 添加 family 映射查询 |
| 6 | C6 哈希链并发 | `app/services/audit_service.py` | 添加 asyncio.Lock 串行化 |
| 7 | C7 异步任务丢失 | `app/security/hsm.py` | task 添加到 _pending_tasks + 完成回调 |
| 8 | C8 降级状态 TTL | `app/security/hsm.py` | TTL 改为 7 天 + 定时续期 |

### 阶段2：High 修复（12项）

| 序号 | 发现 | 修复文件 |
|------|------|---------|
| 1 | H1 密钥分离 | `app/security/hsm.py` |
| 2 | H2 降级行为完善 | `app/security/hsm.py` |
| 3 | H3 SoftHSM 告警 | `app/security/hsm.py` |
| 4 | H4 旧密钥自动清理 | `app/security/hsm.py` |
| 5 | H5 广播补偿 | `app/repositories/consent_repository.py` |
| 6 | H6 SG 单例线程安全 | `safety_gateway/checker.py` |
| 7 | H7 同意权限控制 | `app/api/v1/consent.py` |
| 8 | H8 熔断器降级逻辑 | `safety_gateway/consent_checker.py` |
| 9 | H9 Redis 家族撤销原子性 | `app/security/refresh_token.py` |
| 10 | H10 加密密钥启动检查 | `app/security/hsm.py` |
| 11 | H11 正则修复 | `desensitization_rules.yaml` + `app/security/desensitization.py` |
| 12 | H12 Redis 恢复时序 | `app/security/hsm.py` |

### 阶段3：Medium 修复（13项）

按 M1-M13 顺序执行，每项修复后运行相关测试验证。

### 阶段4：Low 改进（10项）

按 L1-L10 顺序执行，重点补充测试计数至 CI≥1000。

### 阶段5：文档更新

对照核心参考文档，将更优编码实践反向更新到文档中：
1. `04-安全合规.md` — 更新降级阶段行为描述（与代码实际行为对齐后）
2. `00b-统一配置参考手册.md` — 补充 HSM 相关配置项（`USE_SOFT_HSM`、`HSM_KEY_ENCRYPTION_KEY` 等）
3. `03-接口规范.md` — 补充应急签发 API 端点定义

### 阶段6：验证

1. 运行 `pytest tests/unit/ tests/benchmark/ -v` 确保所有测试通过
2. 统计测试计数确认 CI≥1000
3. 运行性能基准确认签名 P99<5ms、撤回<500ms
4. 运行 `pytest --co -q` 统计最终测试数量

---

## 六、关键验证指标

| 指标 | 目标值 | 当前状态 | 修复后预期 |
|------|--------|---------|-----------|
| CI 测试用例数 | ≥1000 | ~681 | ≥1000（补充 Redis/E2E 测试） |
| HSM 签名 P99 | <5ms | benchmark 已覆盖（Mock 环境） | 需添加真实 SoftHSM 测试 |
| 同意撤回生效时间 | <500ms | SG 输出前校验可满足 | 端到端需验证 |
| 脱敏 P99 | <5ms | benchmark 已覆盖 | 保持 |
| SafetyGateway P99 | <5ms | benchmark 已覆盖（Mock consent） | 需添加真实 DB 测试 |
| Critical 发现数 | 0 | 8 | 0 |
| High 发现数 | 0 | 12 | 0 |
