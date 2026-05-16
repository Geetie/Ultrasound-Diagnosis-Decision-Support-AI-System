# SEC安全合规轨道系统性审查计划 v6

## 审查目标

对SEC安全合规轨道（Phase 0 ~ Phase 1）的所有验证环节和测试逻辑进行全面、系统性审查，确保：
- **CI ≥ 1000条测试**（当前约1,415条，已达标）
- **签名P99 < 5ms**
- **撤回后 < 500ms生效**

审查原则：独立、客观、深入、批判性。

---

## 历史审查问题追踪

### P0级问题状态

| 编号 | 问题 | v5状态 | 当前状态 | 说明 |
|------|------|--------|---------|------|
| P0-1 | ConsentStatus枚举不一致 | 未修复 | **已修复** | 主服务和SG均含GRANTED/WITHDRAWN/DENIED/PENDING |
| P0-2 | 撤回SLA基准使用MockHandler | 未修复 | **部分修复** | 已新增真实Handler基准，但MockSafetyActionHandler无效测试仍存在 |
| P0-3 | HSM签名填充方式文档与实现不一致 | 未修复 | **已修复** | sign()用PSS，sign_jwt()用PKCS1v15，测试名称已对齐 |
| P0-4 | HSM Manager未await协程 | 未修复 | **部分修复** | _safe_run_async_task处理主路径，但RuntimeError分支仍可能产生RuntimeWarning |

### P1级问题状态

| 编号 | 问题 | v5状态 | 当前状态 | 说明 |
|------|------|--------|---------|------|
| P1-1 | all_passed语义偏差 | 未修复 | **未修复** | 消息写"四项校验全部通过"，实际执行5个阶段 |
| P1-2 | AuditService构造函数签名不一致 | 未修复 | **未修复** | 6处无参调用，运行时必抛TypeError |
| P1-3 | 限流仅使用内存计数器 | 未修复 | **已修复** | _check_rate_limit_persistent()使用Redis Lua脚本 |
| P1-4 | Compact密钥轮换文档未更新 | 未修复 | **部分修复** | 脱敏密钥compact=60天已实现；JWT密钥compact仍为120天未调整 |
| P1-5 | SG consent_checker使用独立DB模型 | 未修复 | **未修复** | SG和主应用各有ConsentRecord，不同ORM Base |
| P1-6 | 撤回消费者默认仅启用safety_action | 未修复 | **未修复** | settings默认["safety_action"]，data_cleanup永远不会执行 |
| P1-7 | 性能基准缺warmup | 未修复 | **部分修复** | 3/5个基准类已有warmup，2个仍缺 |
| P1-8 | Pytest marks未注册 | 未修复 | **已修复** | pyproject.toml已完整注册 |

### P2级问题状态（v5报告的13项）

| 编号 | 问题 | 当前状态 |
|------|------|---------|
| P2-1 | JWT签名填充方式文档缺失 | 需验证 |
| P2-2 | 脱敏正则异构性可加强 | 需验证 |
| P2-3 | SG客户端熔断器参数硬编码 | 需验证 |
| P2-4 | 审计链锁LRU淘汰策略风险 | 需验证 |
| P2-5 | 代码冗余 | 需验证 |
| P2-6 | Redis客户端重复获取 | 需验证 |
| P2-7 | 数据保留策略缺定时任务 | 需验证 |
| P2-8 | 缺患者数据导出API | 需验证 |
| P2-9 | 缺API Key生命周期管理 | 需验证 |
| P2-10 | 缺边缘端DICOM元数据剥离 | 需验证 |
| P2-11 | 503错误码映射不当 | 需验证 |
| P2-12 | health_probe间隔硬编码 | 需验证 |
| P2-13 | SQLAlchemy废弃导入方式 | 需验证 |

---

## 审查阶段设计

### 阶段1：设计规范一致性深度审查

对照6份核心参考文档，逐项验证实现与规范的一致性。

#### 1.1 三层脱敏架构一致性

| 审查项 | 参考文档 | 验证方法 | 预期结果 |
|--------|---------|---------|---------|
| 三重防线实现完整性 | 04-安全合规.md | 验证三层独立实现且异构 | 标准化层→融合层→SG层，三层正则模式不同源 |
| all_passed语义 | 04-安全合规.md | 检查orchestrator逻辑 | 仅SG passed=False或degraded_mode时all_passed=False |
| 双重降级场景处理 | 04-安全合规.md | 验证降级输出逻辑 | 仅observations白名单+conclusion_suppressed=true |
| 脱敏规则覆盖GB/T 35273 | 04-安全合规.md | 验证6类PII脱敏规则 | 姓名→令牌、身份证→全掩码、手机→后4位、住址→市级、出生日期→仅年份、年龄→不脱敏 |
| 脱敏映射表AES-256-GCM | 04-安全合规.md | 验证加密算法 | 每条记录独立加密，密钥与JWT签名密钥分离 |
| 映射表密钥轮换180天/60天 | 04-安全合规.md | 检查settings配置 | full=180天，compact=60天 |
| 映射表P99<10ms | 04-安全合规.md | 基准测试验证 | 含HSM解密P99<10ms |
| 映射表访问频率监控 | 04-安全合规.md | 检查告警逻辑 | compact>100次/小时、full>200次/小时 |

#### 1.2 HSM密钥管理一致性

| 审查项 | 参考文档 | 验证方法 | 预期结果 |
|--------|---------|---------|---------|
| 密钥槽隔离(4个) | 04-安全合规.md | 验证KeySlot枚举 | JWT_SIGNING/DATA_ENCRYPTION/DESENSITIZATION_MAPPING/EMERGENCY_SIGNING |
| 降级状态机(4阶段) | 04-安全合规.md | 验证DegradedPhase枚举和转换逻辑 | NORMAL→CACHED→RESTRICTED→EMERGENCY |
| JWT签名RS256(PKCS1v15) | 04-安全合规.md | 验证sign_jwt()填充 | PKCS1v15（符合JWT RS256规范） |
| 数据签名RSASSA-PSS | 04-安全合规.md | 验证sign()填充 | PSS+MGF1(SHA256) |
| 密钥轮换120天+7天并行期 | 04-安全合规.md | 验证rotate_key()逻辑 | 旧密钥保留7天 |
| Compact密钥轮换60天 | 04-安全合规.md | 验证compact模式覆盖 | **JWT密钥compact下也应60天？当前未覆盖** |
| SoftHSM 90天过期警告 | 04-安全合规.md | 验证过期检查逻辑 | 超90天警告，120天严重告警 |
| HSM不可用0-5分钟降级 | 04-安全合规.md | 验证降级行为 | 本地会话密钥缓存(TTL 5分钟)+Critical告警 |
| HSM不可用5-30分钟受限 | 04-安全合规.md | 验证受限模式 | 暂停新会话(E4007)+应急签发模式 |
| HSM不可用>30分钟紧急 | 04-安全合规.md | 验证紧急响应 | 评估本地HSM/HSM代理方案 |
| 签名P99<5ms | 04-安全合规.md | 基准测试验证 | HSM签名P99<5ms |
| HSM连接池10连接 | 04-安全合规.md | 验证连接池配置 | 默认10连接 |
| 本地会话密钥缓存TTL 5分钟 | 04-安全合规.md | 验证缓存TTL | TTL=5分钟 |

#### 1.3 患者同意管理一致性

| 审查项 | 参考文档 | 验证方法 | 预期结果 |
|--------|---------|---------|---------|
| 同意状态枚举(granted/withdrawn/denied/pending) | 00a-术语表.md | 验证ConsentStatus枚举 | 主服务和SG一致 |
| 撤回广播Redis Streams | 04-安全合规.md | 验证consent:withdrawal流 | 3次指数退避重试 |
| 撤回后<500ms生效 | 04-安全合规.md | 基准测试验证 | **当前MockHandler基准无效** |
| SG直查PG不使用缓存 | 01-系统架构设计.md | 验证ConsentChecker逻辑 | 直查PG，P99<50ms |
| 撤回后返回E1010 | 03-接口规范.md | 验证错误码映射 | HTTP 403 + E1010 |
| 未签署返回E1008 | 03-接口规范.md | 验证错误码映射 | HTTP 403 + E1008 |
| 检查中撤回：取消在途诊断 | 04-安全合规.md | 验证SafetyActionHandler | 标记withdrawn+通知Edge停止AI标注 |
| 检查后撤回：标记AI部分 | 04-安全合规.md | 验证DataCleanupHandler | **默认未启用** |
| 同意状态权威校验直查PG | 00b-统一配置参考手册.md | 验证不使用缓存 | Redis缓存仅入口快速拒绝 |

#### 1.4 Refresh Token轮换一致性

| 审查项 | 参考文档 | 验证方法 | 预期结果 |
|--------|---------|---------|---------|
| Token Family重放检测 | 04-安全合规.md | 验证family机制 | 检测到重放撤销整个family |
| JWT有效期8小时 | 04-安全合规.md | 验证token TTL | access_token=8小时 |
| 刷新令牌有效期7天 | 04-安全合规.md | 验证refresh_token TTL | 7天 |
| 单用户最大并发会话5个 | 04-安全合规.md | 验证MAX_CONCURRENT_SESSIONS_PER_USER | 第6个会话踢出最早 |
| 私钥存储HSM | 04-安全合规.md | 验证密钥不在配置文件/环境变量 | HSM保护 |
| 泄露应急：立即轮换→作废所有Token | 04-安全合规.md | 验证应急流程 | 强制重新认证+安全事件记录 |

#### 1.5 错误码体系一致性

| 审查项 | 参考文档 | 验证方法 | 预期结果 |
|--------|---------|---------|---------|
| E1008患者未签署 | 03-接口规范.md | 搜索代码中E1008使用 | HTTP 403 |
| E1010患者已撤回 | 03-接口规范.md | 搜索代码中E1010使用 | HTTP 403 |
| E3002三重校验不通过 | 03-接口规范.md | 搜索代码中E3002使用 | HTTP 200 |
| E4005安校验不可用 | 01-系统架构设计.md | 搜索代码中E4005使用 | HTTP 503 |
| E4005A SG模块故障 | 03-接口规范.md | 搜索代码中E4005A使用 | HTTP 503 |
| E4005B PG不可用 | 03-接口规范.md | 搜索代码中E4005B使用 | HTTP 503 |
| E4005C SG+融合层不可用 | 03-接口规范.md | 搜索代码中E4005C使用 | HTTP 503 |
| E4007 HSM不可用 | 04-安全合规.md | 搜索代码中E4007使用 | HTTP 503 |
| E3006/E3007 | 03-接口规范.md | 搜索代码中E3006/E3007 | **v4报告称未实现** |

#### 1.6 数据保留策略一致性

| 审查项 | 参考文档 | 验证方法 | 预期结果 |
|--------|---------|---------|---------|
| 门诊15年/住院30年 | 04-安全合规.md | 验证DataRetentionService | 诊断日志+审计日志 |
| 操作日志5年 | 04-安全合规.md | 验证purge_expired_operation_logs | 5年后删除 |
| 会话热缓存4小时TTL | 04-安全合规.md | 验证Redis TTL | 最大4小时 |
| 会话温存储7天 | 04-安全合规.md | 验证归档逻辑 | 7天快速回看 |
| 审计日志哈希链 | 04-安全合规.md | 验证AuditChain | SHA-256链式+跨链+Merkle Tree |

---

### 阶段2：代码质量深度审查

逐文件审查8个核心模块的代码质量。

#### 2.1 HSMManager (hsm.py, 1051行)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 并发安全 | _safe_run_async_task RuntimeError分支 | 未关闭协程→RuntimeWarning |
| 资源泄漏 | _pending_tasks集合管理 | cleanup_pending_tasks是否在所有退出路径调用 |
| 错误处理 | _restore_keys_from_redis键名解析 | 字符串分割可能误解析 |
| 安全漏洞 | sign_jwt()使用PKCS1v15 | JWT规范要求，但PKCS1v15对选择密文攻击不够健壮 |
| 降级策略 | 4阶段降级状态机转换条件 | 边界条件是否完整 |
| 性能瓶颈 | Redis持久化异步操作 | 高频签名时_pending_tasks积压风险 |

#### 2.2 DesensitizationEngine (desensitization.py, 398行)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 数据一致性 | _name_hmac_key每次重启重新生成 | 跨重启令牌映射丢失 |
| 功能完整性 | 浅比较desensitized_data != data | 嵌套dict可能不准确 |
| 热更新 | Redis PubSub规则同步 | FusionDesensitizationEngine未实现 |

#### 2.3 DesensitizationRestoreService (desensitization_restore.py, 376行)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 运行时崩溃 | AuditService()无参调用(2处) | TypeError |
| 限流一致性 | 内存+Redis双层级限流 | 内存超限→Redis必超限，短路合理 |

#### 2.4 EmergencySigningManager (emergency_signing.py, 315行)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 运行时崩溃 | AuditService()无参调用(4处) | TypeError |
| 审计完整性 | 签名后未写入待验证队列 | 审计链可能断裂 |
| 双人验证 | operator≠approver检查 | 是否可绕过 |

#### 2.5 RefreshTokenStore (refresh_token.py, 870行)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 原子性 | rotate_refresh_token撤销+创建非原子 | 新token创建失败→用户锁出 |
| TTL正确性 | Redis SETEX重置TTL | token意外延长过期 |
| 死循环风险 | login()的while循环踢出逻辑 | get_user_token_count始终>max→无限循环 |
| 冗余操作 | verify+revoke双重撤销 | verify已撤销family后revoke冗余 |

#### 2.6 ConsentWithdrawalConsumer (consent_withdrawal_consumer.py, 367行)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 默认配置 | 仅启用safety_action | data_cleanup永远不会执行 |
| 空实现 | SchedulingAgentHandler | 仅打日志，无实际功能 |
| 并发安全 | SafetyActionHandler无行级锁 | 并发修改consent_status可能覆盖 |

#### 2.7 ConsentChecker (consent_checker.py, 155行)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 数据源一致性 | SG使用独立ConsentRecord模型 | 不同ORM Base，可能查不同数据库 |
| 降级策略 | PG不可用时无"最近已知状态"降级 | 所有诊断输出被拒绝 |

#### 2.8 AuditService / AuditChain (audit_service.py + audit_chain.py)

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 序列化确定性 | session_hashes json.dumps未sort_keys | 验证误报风险 |
| 后台任务恢复 | cross_chain_periodic_task无异常恢复 | 异常后不再重启 |
| groupby排序 | verify_all()的groupby前提 | 同action日志不连续→结果不正确 |
| 敏感信息 | 哈希内容包含before_value/after_value | 哈希本身可能泄露信息 |

---

### 阶段3：代码风格与规范审查

| 维度 | 审查项 | 检查方法 |
|------|--------|---------|
| 类型注解 | Union vs \| 语法一致性 | 全局搜索 `str \| None` vs `Optional[str]` |
| 命名约定 | 函数/变量命名一致性 | 检查下划线vs驼峰混用 |
| 异步模式 | async/sync混用规范 | 检查_safe_run_async_task使用模式 |
| 日志规范 | 结构化日志字段名一致性 | 检查logger.error/warning的action=参数 |
| 错误处理 | 异常类型使用一致性 | 检查自定义异常vs通用Exception |
| 单例模式 | 单例创建线程安全 | 检查DesensitizationEngine/HSMManager等 |

---

### 阶段4：功能正确性边界验证

#### 4.1 三层脱敏边界场景

| 场景 | 验证方法 | 预期行为 |
|------|---------|---------|
| 嵌套dict深度>10层 | 构造深度嵌套数据 | 递归脱敏不栈溢出 |
| set/tuple内含PII | 构造含PII的set/tuple | 正确脱敏（v4报告N5: set内dict未递归脱敏） |
| 空字符串/None值 | 传入空值 | 不崩溃，返回原值 |
| 超长字符串(>10KB) | 传入超长PII | 正则不超时 |
| Unicode/emoji姓名 | 非ASCII字符 | 正确哈希映射 |
| 融合层降级+SG正常 | 模拟融合层降级 | SG独立兜底 |
| 融合层+SG同时降级 | 模拟双重降级 | 仅observations白名单+conclusion_suppressed |

#### 4.2 HSM降级边界场景

| 场景 | 验证方法 | 预期行为 |
|------|---------|---------|
| HSM启动时不可用 | 模拟HSM连接失败 | 启动时进入CACHED阶段 |
| 降级5分钟边界 | 模拟恰好5分钟不可用 | CACHED→RESTRICTED转换 |
| 降级30分钟边界 | 模拟恰好30分钟不可用 | RESTRICTED→EMERGENCY转换 |
| 密钥轮换期间签名 | 触发rotate_key同时签名 | 旧密钥并行期验证通过 |
| SoftHSM恰好90天 | 模拟过期边界 | 警告但不阻止 |
| SoftHSM恰好120天 | 模拟过期边界 | 严重告警+禁止签名 |
| RuntimeError分支 | 无事件循环时调用 | **应关闭协程避免RuntimeWarning** |

#### 4.3 Token轮换边界场景

| 场景 | 验证方法 | 预期行为 |
|------|---------|---------|
| 重放已撤销token | 使用已撤销refresh_token | 撤销整个family |
| 重放不存在的token | 使用伪造refresh_token | 返回错误，不撤销family |
| 第6个会话登录 | 5个活跃会话后登录 | 踢出最旧会话 |
| Redis不可用时轮换 | 模拟Redis连接失败 | MemoryStore降级行为 |
| rotate中创建新token失败 | 模拟新token创建失败 | **当前：用户锁出** |

#### 4.4 同意撤回边界场景

| 场景 | 验证方法 | 预期行为 |
|------|---------|---------|
| 撤回时无活跃会话 | 无在途诊断时撤回 | 仅更新状态，无SafetyAction |
| 撤回广播Redis失败 | 模拟Redis Streams写入失败 | 3次指数退避重试 |
| 撤回后立即查询 | 撤回后<500ms查询 | 返回withdrawn |
| PG不可用时撤回 | 模拟PG连接失败 | **当前：所有输出被拒绝，无降级** |
| 并发撤回同一患者 | 两个请求同时撤回 | 幂等处理 |

#### 4.5 应急签发边界场景

| 场景 | 验证方法 | 预期行为 |
|------|---------|---------|
| operator=approver | 同一人同时操作和审批 | 拒绝 |
| 达到每日签名上限 | 签名次数达限 | 拒绝+告警 |
| Redis持久化计数失败 | 模拟Redis不可用 | **计数可能丢失** |

#### 4.6 密钥轮换边界场景

| 场景 | 验证方法 | 预期行为 |
|------|---------|---------|
| 轮换期间验证旧token | 用旧密钥签名的JWT验证 | 7天并行期内验证通过 |
| 轮换后验证旧token | 并行期过后验证 | 验证失败 |
| 轮换重加密吞吐 | 百万级映射表重加密 | >=1000条/秒 |
| compact模式JWT轮换 | compact模式下JWT密钥轮换 | **当前：仍120天，未缩短** |

---

### 阶段5：测试逻辑有效性审查

#### 5.1 测试覆盖率评估

| 模块 | 测试文件数 | 测试函数数 | 覆盖缺口 |
|------|-----------|-----------|---------|
| 三层脱敏 | 2+ | ~784 | ThreeLayerDesensitizationManager无独立测试文件 |
| HSM密钥管理 | 3+ | ~758 | _migrate_legacy_keys()无测试 |
| 患者同意 | 2+ | ~364 | DataCleanupHandler无独立测试；ConsentPhase降级路径无E2E测试 |
| Refresh Token | 1+ | ~362 | RedisRefreshTokenStore缺集成测试 |
| 数据保留 | 1 | ~164 | 无定时任务测试 |
| 审计哈希链 | 1 | ~167 | AuditCrossChain.record_cross_chain()/verify_cross_chain()无测试 |
| 综合回归 | 3 | ~1,275 | — |
| **总计** | **13+** | **~3,874** | — |

#### 5.2 测试正确性审查

| 审查维度 | 审查项 | 已知问题 |
|---------|--------|---------|
| 断言有效性 | MockHandler基准测试断言p99<500ms | 测量的是"往列表追加元素"的延迟，基准数据无效 |
| Mock合理性 | AuditService()无参Mock | 隐藏了6处运行时TypeError |
| 测试隔离性 | conftest.py的reset_sec_singletons | 是否覆盖所有单例 |
| 负面测试 | 异常路径覆盖 | E4005A1/E4005B/E4005C错误码路径未验证 |
| 性能测试可靠性 | warmup步骤 | 2个基准类仍缺warmup |

#### 5.3 新增测试需求

| 优先级 | 测试需求 | 原因 |
|--------|---------|------|
| P0 | 替换MockSafetyActionHandler基准测试 | 当前基准数据无效 |
| P0 | 添加AuditService无参调用检测测试 | 6处运行时TypeError |
| P1 | RedisRefreshTokenStore集成测试 | 生产环境可能启用但未验证 |
| P1 | ConsentPhase降级路径E2E测试 | E4005A1/E4005B/E4005C未验证 |
| P1 | AuditCrossChain跨链验证测试 | record_cross_chain()/verify_cross_chain()无测试 |
| P1 | EmergencySigningManager.verify_emergency_signature()测试 | 签名验证流程未覆盖 |
| P2 | DataCleanupHandler独立测试 | 仅在consumer集成测试中间接覆盖 |
| P2 | 数据保留定时任务测试 | 无自动调度验证 |

---

### 阶段6：文档一致性交叉审查

对照6份核心参考文档，识别文档与实现的偏差，决定是更新代码还是更新文档。

#### 6.1 需要更新的文档

| 文档 | 偏差项 | 建议方向 |
|------|--------|---------|
| 04-安全合规.md | JWT签名填充方式说明 | 补充sign_jwt()用PKCS1v15、sign()用PSS的说明 |
| 04-安全合规.md | Compact JWT密钥轮换周期 | 若确认compact应60天，则更新文档；若确认120天合理，则补充说明 |
| 03-接口规范.md | E3006/E3007定义 | 若确认不需要，则从文档删除；若需要，则补充实现 |
| 00a-术语表.md | ConsentStatus.PENDING说明 | 补充PENDING状态的使用场景说明 |
| 00b-统一配置参考手册.md | SG_CONSENT_CACHE_TTL_SECONDS | 补充此配置项的文档说明 |
| 00b-统一配置参考手册.md | SG_DB_RETRY_MAX_TOTAL_TIME_SECONDS | SG=15s vs 文档=10s，需对齐 |

#### 6.2 需要更新的代码

| 代码文件 | 偏差项 | 建议方向 |
|---------|--------|---------|
| orchestrator.py | "四项校验全部通过"消息 | 更新为"五项校验全部通过" |
| settings.py | consent_withdrawal_handlers默认值 | 改为["safety_action", "data_cleanup"] |
| hsm.py | _safe_run_async_task RuntimeError分支 | 添加coro.close() |
| desensitization_restore.py | AuditService()无参调用(2处) | 传入db session |
| emergency_signing.py | AuditService()无参调用(4处) | 传入db session |

---

## 执行计划

### 步骤1：修复已知P0级遗留问题

1. **P0-2遗留**：删除或替换`TestRevocationLatencyBenchmark`中的`MockSafetyActionHandler`基准测试
2. **P0-4遗留**：在`_safe_run_async_task`的`except RuntimeError`分支添加`coro.close()`

### 步骤2：修复已知P1级遗留问题

1. **P1-1**：更新orchestrator.py消息文本
2. **P1-2**：修复6处AuditService()无参调用
3. **P1-4**：确认compact JWT密钥轮换策略，更新代码或文档
4. **P1-5**：统一SG和主应用的ConsentRecord模型
5. **P1-6**：更新settings.py默认handler列表
6. **P1-7**：为2个缺warmup的基准类添加warmup

### 步骤3：执行设计规范一致性审查

按阶段1的审查清单逐项验证，记录MATCH/MISMATCH结果。

### 步骤4：执行代码质量深度审查

按阶段2的审查清单逐文件审查，记录问题。

### 步骤5：执行功能正确性边界验证

按阶段4的边界场景构造测试用例，验证行为。

### 步骤6：执行测试逻辑有效性审查

按阶段5的审查清单评估测试质量，补充缺失测试。

### 步骤7：执行文档一致性交叉审查

按阶段6的审查清单对照文档，决定更新方向。

### 步骤8：生成审查报告

汇总所有审查结果，生成SEC安全合规轨道系统性审查报告v6。

---

## 量化指标验证

| 指标 | 目标值 | 当前状态 | 验证方法 |
|------|--------|---------|---------|
| CI测试总数 | ≥1000 | ~1,415 ✅ | pytest --co -q |
| SEC单元测试通过率 | 100% | 待验证 | pytest tests/unit/sec/ |
| SG单元测试通过率 | 100% | 待验证 | pytest tests/unit/sg/ |
| 签名P99延迟 | <5ms | 待验证 | pytest tests/benchmark/sec/ -m track_sec |
| 撤回生效延迟 | <500ms | **Mock基准无效** ⚠️ | 需修复后重新验证 |
| 脱敏映射表P99 | <10ms | 待验证 | 基准测试 |
| SG端到端P99 | <50ms | 待验证 | 基准测试 |
| 设计规范一致性 | ≥90% | 82.4%(v4) | 逐项对照 |

---

## 风险评估

| 风险等级 | 风险描述 | 影响 | 缓解措施 |
|---------|---------|------|---------|
| **严重** | AuditService()6处无参调用 | 运行时TypeError，审计日志丢失 | 立即修复 |
| **严重** | data_cleanup handler默认未启用 | 撤回后脱敏缓存未清理，数据泄露风险 | 立即修复默认配置 |
| **高** | SG ConsentRecord独立模型 | 可能查不同数据库，同意状态不一致 | 统一模型或明确数据源 |
| **高** | MockHandler基准测试无效 | 撤回SLA<500ms未经验证 | 替换为真实Handler基准 |
| **中** | compact JWT轮换未缩短 | 密钥暴露窗口比预期长 | 评估并决定策略 |
| **中** | _safe_run_async_task协程泄漏 | RuntimeWarning，极端情况数据丢失 | 添加coro.close() |
| **低** | all_passed消息文本不准确 | 日志误导 | 更新消息文本 |
