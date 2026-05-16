# SEC安全合规轨道系统性审查计划

## 审查范围

对SEC轨道（三层脱敏、HSM密钥管理、患者同意管理、Refresh Token轮换）的所有验证环节和测试逻辑进行全面、系统性审查，涵盖设计规范合规性、代码质量、代码风格、功能正确性四个维度，并对照核心参考文档进行一致性审查。

---

## 第一阶段：设计规范合规性审查

### 1.1 三层脱敏 vs 04-安全合规/01-系统架构

| 审查项 | 文档要求 | 当前实现 | 问题 |
|---|---|---|---|
| 三重防线职责分层 | 标准化层(结构化字段)→融合层(LLM自由文本ML NER)→SafetyGateway(正则匹配兜底) | 第一层用正则、第二层用正则、第三层用正则+人名词典 | **融合层未实现ML NER**，与文档"LLM自由文本ML NER"要求不符 |
| 双重降级场景保障 | 融合层不可用→仅observations; SafetyGateway不可用→拒绝所有输出(E4005) | checker.py实现了降级逻辑 | 需验证降级路径完整性 |
| 脱敏映射表加密 | AES-256-GCM加密，HSM管理密钥 | desensitization_restore.py用RSA公钥PEM前32字节作AES密钥 | **严重偏差**：未使用HSM管理的对称密钥，而是从RSA公钥派生，违反"HSM管理密钥"要求 |
| 密钥轮换 | 180天轮换 | hsm_manager有rotate_key方法，但无自动轮换调度 | 缺少自动轮换机制 |
| 访问频率监控 | compact 100次/h, full 200次/h | _check_rate_limit使用小时级计数器 | 未区分compact/full Profile |

### 1.2 HSM密钥管理 vs 04-安全合规

| 审查项 | 文档要求 | 当前实现 | 问题 |
|---|---|---|---|
| Compact Profile密钥管理 | 默认USB HSM令牌(YubiHSM 2)，临时过渡SoftHSM(90天必须替换) | _check_soft_hsm_expiry检查90天，_start_soft_hsm_periodic_check定期检查 | SoftHSM超期仅告警不阻止操作（sign方法检查了，但encrypt/decrypt未检查） |
| HSM长时间不可用 | 0-5分钟本地缓存→5-30分钟受限模式→>30分钟紧急运维 | DegradedPhase枚举+DEGRADED_BEHAVIORS映射 | 阶段时间阈值与文档一致 |
| 紧急签名 | 每日签名次数限制、双因子验证 | EmergencySigningManager有_daily_signature_count | **verify_emergency_signature存在bug**：将signature_data同时作为data和signature参数传入verify_signature |
| JWT密钥轮换 | JWT密钥HSM存储，120天轮换 | 无自动轮换调度机制 | 缺少120天轮换实现 |

### 1.3 患者同意管理 vs 04-安全合规/01-系统架构

| 审查项 | 文档要求 | 当前实现 | 问题 |
|---|---|---|---|
| 检查中撤回 | 在途诊断取消+输出拦截+边缘端停止标注 | SafetyActionHandler标记session+发布Redis通知+边缘端停止标注 | **DataCleanupHandler未实现**，数据清理逻辑缺失 |
| 检查后撤回 | HIS/PACS同步撤回通知 | 无实现 | 缺失 |
| 撤回后生效延迟 | <500ms | 无测试验证 | 缺少延迟验证 |
| 同意状态缓存 | SafetyGateway直查PostgreSQL，降级Redis只读缓存 | consent_checker.py实现了直查PG+Redis降级 | 符合要求 |

### 1.4 Refresh Token轮换 vs 04-安全合规

| 审查项 | 文档要求 | 当前实现 | 问题 |
|---|---|---|---|
| Token Family机制 | 检测重放攻击时吊销整个Family | verify_refresh_token检测已撤销token时触发revoke_token_family | 功能已实现 |
| 会话互踢 | JWT 8小时有效期，会话互踢 | _enforce_concurrent_session_limit实现 | 符合要求 |
| Redis持久化 | 生产环境使用Redis存储 | RedisRefreshTokenStore已实现 | **但完全无测试覆盖** |

---

## 第二阶段：代码质量审查

### 2.1 安全漏洞

| 编号 | 文件 | 问题描述 | 严重度 |
|---|---|---|---|
| SEC-001 | desensitization_restore.py#L53-58 | AES密钥从RSA公钥PEM前32字节派生，非HSM管理的对称密钥。公钥PEM是可预测的格式化文本，降低密钥熵值 | **高** |
| SEC-002 | emergency_signing.py#L207 | `verify_signature(jwt_signing_key, signature_data, signature_data)` 将签名数据同时作为原文和签名传入，验证逻辑错误 | **高** |
| SEC-003 | hsm.py#L382-387 | 私钥序列化时若无HSM_PRIVATE_KEY_PASSWORD则使用NoEncryption，私钥明文存入Redis | **高** |
| SEC-004 | hsm.py#L838-845 | HSM_KEY_ENCRYPTION_KEY从环境变量读取，若未设置则抛异常，但异常消息中提示"Set HSM_KEY_ENCRYPTION_KEY"，可能引导用户设置弱密钥 | **中** |
| SEC-005 | desensitization_restore.py#L74-82 | _decrypt_value在key_version不匹配时仅打warning仍继续解密，可能导致解密失败或使用错误密钥 | **中** |
| SEC-006 | refresh_token.py#L640 | USE_REDIS_STORAGE环境变量控制存储后端选择，默认false使用内存存储，生产部署若遗漏配置则Token不持久化 | **中** |

### 2.2 健壮性问题

| 编号 | 文件 | 问题描述 |
|---|---|---|
| ROB-001 | hsm.py#L167-201 | _safe_run_async_task混合同步/异步调用，在无事件循环时数据可能丢失（已记录日志但未阻止操作继续） |
| ROB-002 | hsm.py#L883-947 | _restore_keys_from_redis在__init__中通过_safe_run_async_task异步执行，密钥可能在未完全恢复时就被使用 |
| ROB-003 | consent_withdrawal_consumer.py#L203-238 | _process_event中字段解码逻辑复杂且冗余（bytes/str双重处理），易引入解码错误 |
| ROB-004 | consent_repository.py#L159-209 | _publish_consent_withdrawal在所有重试失败后创建SafetyEvent，但未await db.commit() |
| ROB-005 | three_layer_desensitization.py#L59 | process_standardization_layer在存储映射时未传递session_id参数（函数签名有但调用处未检查是否为None） |

### 2.3 架构缺陷

| 编号 | 文件 | 问题描述 |
|---|---|---|
| ARC-001 | hsm.py | HSMManager类998行，承担密钥生成/签名/加密/降级/持久化/迁移全部职责，违反单一职责原则 |
| ARC-002 | refresh_token.py | 模块级函数(create_refresh_token等)通过isinstance判断存储类型分发调用，应使用策略模式或工厂方法 |
| ARC-003 | three_layer_desensitization.py | ThreeLayerDesensitizationManager直接import具体实现（desensitization_engine等），应依赖抽象接口 |

---

## 第三阶段：代码风格审查

### 3.1 一致性问题

| 编号 | 文件 | 问题描述 |
|---|---|---|
| STY-001 | 多文件 | 单例模式实现不统一：DesensitizationEngine用__new__、HSMManager用模块级变量、ThreeLayerDesensitizationManager用函数+Lock |
| STY-002 | hsm.py vs consent_checker.py | 时间计算方式不一致：hsm用datetime差值，consent_checker用asyncio.get_running_loop().time() |
| STY-003 | refresh_token.py | MemoryRefreshTokenStore同步方法 vs RedisRefreshTokenStore异步方法，接口定义(RefreshTokenStore)为同步但Redis实现为async |
| STY-004 | 多文件 | 日志字段命名不一致：有些用snake_case（consent_check_timeout），有些用camelCase（未发现但需检查） |

### 3.2 可读性问题

| 编号 | 文件 | 问题描述 |
|---|---|---|
| READ-001 | consent_withdrawal_consumer.py#L203-221 | _process_event中字段提取的三元表达式嵌套过深，可读性差 |
| READ-002 | hsm.py#L617-672 | rotate_key方法过长，应拆分为_backup_old_key和_generate_new_key |
| READ-003 | checker.py#L688-770 | apply_degraded_output方法中reason判断使用长if-elif链，应使用策略字典 |

---

## 第四阶段：功能正确性审查

### 4.1 脱敏规则正确性

| 编号 | 规则 | 测试用例 | 预期 | 实际 | 问题 |
|---|---|---|---|---|---|
| DSN-001 | birth_date | "1995-05-15出生" | "1995-**-**出生" | "****-**-**" | **替换了年份**，与文档"仅保留年份"矛盾 |
| DSN-002 | patient_name_simple | "王明，男，45岁" | "[REDACTED_NAME]，男，45岁" | 需验证 | 规则`^([\u4e00-\u9fa5]{2,4})(?=，|,\|$)`可能误匹配非姓名的2-4字中文 |
| DSN-003 | id_card | "110101199003071234" | "********************" | 需验证 | 文档要求"仅后4位"但规则替换为全星号 |
| DSN-004 | phone | "13812345678" | "******5678" | "******5678" | 符合文档"仅后4位" |
| DSN-005 | address | "北京市朝阳区建国路" | "北京市***" | "北京市***" | 符合文档"仅保留到市级" |

**关键偏差**：04-安全合规规定"身份证号→仅后4位"，但代码中id_card规则替换为全星号（********************），与文档不一致。需确认哪个为准——若全星号更安全则应更新文档。

### 4.2 SafetyGateway校验流程正确性

| 编号 | 场景 | 预期行为 | 需验证项 |
|---|---|---|---|
| SG-001 | consent=WITHDRAWN + DB不可用 | 拒绝所有输出(E4005A) | checker.py#L342-355 已实现 |
| SG-002 | consent=GRANTED + DB超时 | 降级输出(仅observations) | checker.py#L394-408 已实现 |
| SG-003 | 脱敏检测到敏感数据 + 融合层不可用 | 拒绝所有输出(E4005A) | checker.py#L452-466 已实现 |
| SG-004 | 禁忌+阈值失败+融合不可用(≥2项) | 组合降级 | checker.py#L542-564 已实现 |
| SG-005 | consent_status_at_input非granted | 入口快速拒绝 | checker.py#L267-287 已实现 |

### 4.3 同意撤回端到端流程

| 步骤 | 组件 | 需验证 |
|---|---|---|
| 1 | consent API | 撤回请求触发ConsentRepository.create_consent |
| 2 | ConsentRepository | 发布Redis Stream事件 |
| 3 | ConsentWithdrawalConsumer | 消费事件并分发handler |
| 4 | SafetyActionHandler | 标记在途session + 通知边缘端 + 审计日志 |
| 5 | SafetyGateway | 后续请求检测到WITHDRAWN状态拒绝输出 |
| **缺失** | DataCleanupHandler | 数据清理未实现 |
| **缺失** | HIS/PACS同步 | 检查后撤回通知未实现 |

---

## 第五阶段：测试逻辑审查

### 5.1 测试覆盖率统计

| 模块 | 实现文件数 | 测试文件数 | 测试用例数 | 关键缺失 |
|---|---|---|---|---|
| 脱敏引擎 | 5 | 2 | 54 | 融合层ML NER未实现故无测试 |
| 脱敏还原 | 2 | 1 | 10 | 无端到端流程、无HSM降级场景 |
| HSM密钥管理 | 2 | 2 | 47 | 无Redis持久化验证、无自动轮换 |
| 患者同意 | 4 | 2 | 22 | 无非特权角色越权、无并发竞争 |
| Refresh Token | 1 | 1 | 18 | **无Token重用检测测试**、无Redis存储测试 |
| SafetyGateway | 17 | 2 | 11 | 无降级恢复测试、无端到端延迟 |
| **合计** | **31** | **10** | **~162** | **CI≥1000目标差距大** |

### 5.2 关键测试缺失清单

| 优先级 | 缺失测试 | 影响范围 | 建议补充用例数 |
|---|---|---|---|
| P0 | Token重用检测（Replay Attack） | Refresh Token安全核心 | 8 |
| P0 | RedisRefreshTokenStore完整测试 | 生产环境Token存储 | 15 |
| P0 | 同意撤回端到端延迟(<500ms) | 合规要求 | 3 |
| P0 | HSM密钥自动轮换(120天/180天) | 合规要求 | 6 |
| P1 | 脱敏还原HSM降级场景 | 数据可恢复性 | 5 |
| P1 | SafetyGateway降级恢复过渡 | 系统可靠性 | 4 |
| P1 | 审计日志完整性验证 | 合规审计 | 6 |
| P1 | 并发竞争条件测试 | 数据一致性 | 8 |
| P2 | 非特权角色越权访问 | 权限安全 | 4 |
| P2 | 脱敏规则优先级/冲突 | 脱敏正确性 | 5 |
| P2 | 密钥版本轮换后旧版本解密 | 数据可恢复性 | 3 |

### 5.3 测试质量问题

| 编号 | 问题 | 文件 |
|---|---|---|
| TST-001 | test_hsm_basic.py与test_security_compliance.py存在大量重复测试类（TestHSMSignaturePadding、TestHSMDegradedState等完全重复） | 多文件 |
| TST-002 | 性能测试百分位数计算方式不统一：test_safety_gateway_performance.py用线性插值，test_revocation_performance.py用简单索引 | benchmark/ |
| TST-003 | test_consent_api.py仅7个测试，覆盖严重不足 | test_consent_api.py |
| TST-004 | test_desensitization_restore.py限流测试直接修改内部计数器，未测试真实连续请求 | test_desensitization_restore.py |
| TST-005 | test_safety_gateway_performance.py仅测试GRANTED状态性能，未测试WITHDRAWN等快速拒绝路径 | benchmark/ |

---

## 第六阶段：实施步骤

### Step 1: 修复安全漏洞（SEC-001~SEC-006）

1. **SEC-001**: 重构`desensitization_restore.py`的`_get_aes_key`，使用HSM生成对称密钥（AES-256）而非从RSA公钥派生
2. **SEC-002**: 修复`emergency_signing.py`的`verify_emergency_signature`，正确传入原始数据和签名
3. **SEC-003**: 强制要求`HSM_PRIVATE_KEY_PASSWORD`环境变量，移除NoEncryption降级路径
4. **SEC-005**: `_decrypt_value`在key_version不匹配时应尝试使用对应版本的密钥解密，而非仅打warning
5. **SEC-006**: 添加启动时配置校验，生产模式强制Redis存储

### Step 2: 补充关键测试（P0级）

1. Token重用检测测试：已撤销Token重用→家族撤销、已过期Token重用→家族撤销、跨Family Token重用
2. RedisRefreshTokenStore测试：CRUD、家族撤销、并发安全、过期清理
3. 同意撤回端到端延迟测试：从撤回到SafetyGateway拒绝输出的完整链路<500ms
4. HSM密钥轮换测试：120天JWT密钥轮换、180天脱敏映射密钥轮换、轮换后旧数据可解密

### Step 3: 修复功能偏差

1. **birth_date规则**: 修正为仅脱敏月日，保留年份（对齐04-安全合规）
2. **id_card规则**: 确认全星号vs仅后4位策略，统一代码与文档
3. **DataCleanupHandler**: 实现同意撤回后的数据清理逻辑
4. **HIS/PACS同步**: 实现检查后撤回通知（至少预留接口）

### Step 4: 代码质量改进

1. 拆分HSMManager为KeyGenerator/KeyStore/DegradationManager
2. 统一单例模式实现方式
3. 重构_process_event字段解码逻辑
4. 统一性能测试百分位数计算

### Step 5: 文档一致性更新

1. 若id_card全星号策略更优，更新04-安全合规文档
2. 若融合层ML NER为一期不实现，在文档中标注阶段说明
3. 补充SoftHSM超期后的具体操作指引

---

## 审查结论摘要

### 总体评估

SEC轨道的核心安全架构（三层脱敏、HSM降级、同意撤回事件驱动、Token Family防重放）设计合理，实现基本完整。但存在以下系统性风险：

1. **密钥管理偏差**：脱敏还原服务未使用HSM管理的对称密钥，是最大的安全合规偏差
2. **测试覆盖不足**：当前~162个测试用例，距CI≥1000目标差距大；Token重用检测、Redis存储等核心场景零覆盖
3. **功能缺失**：DataCleanupHandler、HIS/PACS同步、自动密钥轮换等文档要求的功能未实现
4. **代码签名验证bug**：emergency_signing的verify方法存在逻辑错误

### 优先修复顺序

1. SEC-001（AES密钥派生）+ SEC-002（签名验证bug）→ 安全漏洞
2. P0测试补充 → 验证保障
3. 功能偏差修复 → 合规对齐
4. 代码质量改进 → 可维护性
