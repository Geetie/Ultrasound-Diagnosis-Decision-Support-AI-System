# SEC安全合规轨道系统性审查报告

**审查日期**：2026-05-11
**审查范围**：Phase 0 ~ Phase 1 SEC轨道全部实现代码与测试
**审查方法**：文档对照 + 代码审查 + 测试执行

---

## 一、测试执行结果总览

| 指标 | 数值 | 目标 | 状态 |
|------|------|------|------|
| SEC相关测试总数 | 944条 | ≥1000条 | ⚠️ 未达标(差56条) |
| 通过 | 901条 | - | - |
| 失败 | 42条 | 0 | ❌ |
| 错误 | 1条 | 0 | ❌ |
| 通过率 | 95.4% | 100% | ❌ |

---

## 二、问题清单

### P0 — 关键问题（影响安全合规核心功能）

#### P0-1: test_e2e_security_flow.py 导入错误
- **文件**: [test_e2e_security_flow.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_e2e_security_flow.py#L14)
- **问题**: `from app.security.auth import create_access_token, verify_token` — `verify_token`函数不存在于auth.py
- **影响**: E2E安全流程测试完全无法收集/运行
- **修复**: 已将导入修正为 `decode_access_token`（auth.py中实际存在的函数）
- **状态**: ✅ 已修复

#### P0-2: SafetyGateway层consent_status强制校验导致测试失败
- **文件**: [three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py#L116)
- **问题**: `process_safety_gateway_layer()` 强制要求 `consent_status_at_input` 不为None，否则抛出ValueError。这是正确的设计行为（与04-安全合规 §6一致），但测试 `test_safety_gateway_layer_has_no_original` 未传入该参数
- **影响**: 1个测试失败
- **修复建议**: 测试需传入 `consent_status_at_input="granted"` 参数
- **状态**: 待修复

#### P0-3: HSM降级事件机制不完整
- **文件**: [test_white_fixes.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_white_fixes.py), [test_white_round2_fixes.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_white_round2_fixes.py)
- **问题**: 5个HSM降级事件相关测试失败（`test_set_degraded_changes_state`, `test_clear_degraded_resets_state`等），表明HSMManager的降级状态变更事件通知机制与测试期望不一致
- **影响**: HSM降级状态管理可靠性存疑
- **修复建议**: 检查HSMManager的set_degraded/clear_degraded方法是否正确触发事件，与测试期望对齐
- **状态**: 待修复

#### P0-4: 同意撤回延迟测试未达标
- **文件**: [test_sec_p0_critical.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_sec_p0_critical.py)
- **问题**: `test_consent_withdrawal_marks_session_within_500ms` 失败
- **影响**: 核心性能指标"撤回后<500ms生效"未验证通过
- **修复建议**: 检查同意撤回处理链路的延迟瓶颈，优化异步事件处理
- **状态**: 待修复

#### P0-5: E2E HSM签名流程测试失败
- **文件**: [test_e2e_security_flow.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_e2e_security_flow.py)
- **问题**: 4个HSM签名E2E测试失败（`test_sign_verify_roundtrip`, `test_sign_verify_tampered_data_fails`, `test_key_slot_isolation`, `test_degraded_phase_restricts_jwt_signing`）
- **影响**: HSM签名核心功能端到端验证缺失
- **修复建议**: 检查HSMManager在测试环境下的初始化和密钥生成是否正确
- **状态**: 待修复

#### P0-6: E2E Token轮换流程测试失败
- **文件**: [test_e2e_security_flow.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_e2e_security_flow.py)
- **问题**: 2个Token轮换E2E测试失败（`test_full_token_lifecycle`, `test_token_replay_detection`）
- **影响**: Token轮换端到端验证缺失
- **修复建议**: 检查E2E测试中Token存储的初始化方式
- **状态**: 待修复

---

### P1 — 重要问题（影响功能完整性或合规性）

#### P1-1: SafetyGateway检查器测试失败
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py)
- **问题**: 3个SafetyGateway检查器测试失败（consent检查、敏感数据检测）
- **影响**: SafetyGateway核心安全校验功能验证缺失
- **修复建议**: 检查SafetyGatewayChecker的consent检查和敏感数据检测逻辑

#### P1-2: 三层脱敏all_passed判断逻辑测试失败
- **文件**: [test_sec_audit.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_sec_audit.py)
- **问题**: `test_all_passed_false_when_degraded` 失败
- **影响**: 降级场景下all_passed判断逻辑可能有误
- **修复建议**: 检查ThreeLayerDesensitizationResult.all_passed在降级场景下的计算逻辑

#### P1-3: 应急签名激活条件测试失败
- **文件**: [test_sec_hsm_deep.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_sec_hsm_deep.py)
- **问题**: `test_emergency_activate_requires_condition` 失败
- **影响**: 应急签名激活条件验证缺失
- **修复建议**: 检查EmergencySigningManager的激活条件判断逻辑

#### P1-4: DICOM存储配置缺失
- **文件**: [test_audit_fixes.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_audit_fixes.py)
- **问题**: 6个DICOM存储相关测试失败（配置项缺失、默认值不匹配）
- **影响**: DICOM文件存储安全配置不完整
- **修复建议**: 在settings.py中补充dicom_storage相关配置项

#### P1-5: SG同意缓存TTL配置不一致
- **文件**: [test_config_doc_consistency.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_config_doc_consistency.py)
- **问题**: `sg_consent_cache_ttl_seconds` 配置与文档不一致
- **影响**: 配置文档可信度降低
- **修复建议**: 对齐代码与文档中的SG同意缓存TTL值

#### P1-6: 患者姓名简单嵌入脱敏测试失败
- **文件**: [test_e2e_security_flow.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_e2e_security_flow.py)
- **问题**: `test_patient_name_simple_embedded` 失败
- **影响**: 患者姓名脱敏覆盖不完整
- **修复建议**: 检查patient_name_simple规则的正则表达式匹配范围

---

### P2 — 一般问题（代码质量/风格/可维护性）

#### P2-1: 测试总数未达CI≥1000条目标
- **当前**: 944条SEC相关测试
- **目标**: ≥1000条
- **差距**: 56条
- **建议**: 补充边界条件测试、降级场景集成测试、性能基准扩展测试

#### P2-2: 脱敏规则中患者姓名替换使用[REDACTED_NAME]而非文档规定的PATIENT_XXX令牌
- **文件**: [desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/desensitization.py#L123)
- **问题**: 默认规则中患者姓名替换使用 `[REDACTED_NAME]`，但 `_get_patient_token()` 方法生成 `PATIENT_001` 格式令牌。默认规则的replacement字段未使用token映射机制
- **影响**: 仅当规则名为 `patient_name`/`patient_name_with_title`/`patient_name_simple` 时才走token映射路径，其他规则名直接使用replacement字符串
- **建议**: 确认这是否为设计意图——默认规则已正确路由到token映射，replacement字段仅作fallback

#### P2-3: 审计链哈希计算未包含hash_prev作为前缀
- **文件**: [audit_service.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/audit_service.py#L63-72)
- **问题**: 哈希计算中 `hash_prev` 被追加在末尾（`hash_content = f"{hash_prev}-{hash_content}"`），而04-安全合规 §3描述的链式结构暗示hash_prev应作为前缀参与计算
- **影响**: 功能上不影响链式完整性（只要计算方式一致），但与文档描述的语义不完全一致
- **建议**: 确认当前实现是否满足合规审计要求

#### P2-4: HSM密钥轮换周期配置与文档差异
- **文件**: [settings.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/config/settings.py#L60)
- **问题**: `desensitization_key_rotation_days=180` 与04-安全合规 §2.1一致，但缺少compact模式60天轮换的差异化配置
- **影响**: compact模式下脱敏映射密钥轮换周期未按文档要求缩短
- **建议**: 增加 `desensitization_key_rotation_days_compact=60` 配置项，或在model_validator中根据deployment_profile自动调整

#### P2-5: JWT密钥轮换120天周期未在代码中体现
- **文件**: [hsm.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py)
- **问题**: 04-安全合规 §4.1规定JWT签名密钥120天轮换，但代码中未找到自动轮换定时器或轮换周期配置
- **影响**: JWT密钥轮换依赖人工操作，存在遗漏风险
- **建议**: 增加JWT密钥自动轮换定时器或启动时检查机制

#### P2-6: HSM降级阶段时间阈值硬编码
- **文件**: [hsm.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/hsm.py#L83-84)
- **问题**: `DEGRADED_PHASE_1_THRESHOLD = timedelta(minutes=5)` 和 `DEGRADED_PHASE_2_THRESHOLD = timedelta(minutes=30)` 硬编码在类中，未通过配置项管理
- **影响**: 不同部署环境可能需要调整降级时间阈值
- **建议**: 将阈值移至settings.py配置项

#### P2-7: asyncio事件循环DeprecationWarning
- **问题**: `hsm.py:173` 触发 `DeprecationWarning: There is no current event loop`
- **影响**: Python 3.12+中事件循环获取方式已变更
- **建议**: 使用 `asyncio.get_event_loop_policy().get_event_loop()` 替换已废弃的调用方式，或改用 `asyncio.run()` 模式

---

## 三、设计规范一致性审查结果

| 审查项 | 状态 | 说明 |
|--------|------|------|
| 三层脱敏职责分层 | ✅ | 标准化层→融合层→SafetyGateway 三层严格分层，职责清晰 |
| SG规则异构冗余 | ⚠️ | SG使用独立规则文件(`desensitization_rules_safety.yaml`)，但未验证规则是否真正异构（非复制主规则） |
| 双重降级场景处理 | ✅ | `three_layer_desensitization.py` 支持fusion_available和desensitization_degraded参数 |
| HSM四槽位定义 | ✅ | JWT_SIGNING/DATA_ENCRYPTION/DESENSITIZATION_MAPPING/EMERGENCY_SIGNING 完整 |
| HSM降级四阶段 | ✅ | NORMAL→CACHED(5min)→RESTRICTED(30min)→EMERGENCY 与文档一致 |
| 密钥轮换7天并行期 | ✅ | `rotate_key()` 保留旧密钥，设置过期时间 |
| SoftHSM 90天/120天阈值 | ✅ | `SOFT_HSM_EXPIRY_DAYS=90`, `SOFT_HSM_CRITICAL_DAYS=120` |
| Compact密钥轮换60天区分 | ❌ | 未实现compact模式60天轮换差异化(P2-4) |
| 应急签发双人复核 | ✅ | `EmergencySigningManager` 强制 `operator_id != approver_id` |
| 同意状态PostgreSQL权威源 | ✅ | consent_repository直写PostgreSQL，无Redis双写 |
| 两阶段同意校验 | ⚠️ | SafetyGateway层强制要求consent_status，但入口快速拒绝缓存(TTL=500ms)实现需验证 |
| Token Family机制 | ✅ | family_id关联、重放检测撤销整个family |
| 重放检测撤销整个family | ✅ | verify_refresh_token中检测到已撤销token自动撤销family |
| 并发会话限制(默认5) | ✅ | `max_concurrent_sessions_per_user=5` |
| 脱敏映射AES-256-GCM加密 | ✅ | `AESGCM` 使用32字节密钥 |
| 密钥版本key_version支持 | ✅ | `DesensitizationMappingRecord.key_version` 和 `_current_key_version` |
| 还原速率限制分档 | ⚠️ | `desensitization_restore_rate_limit=100` 未区分compact/full(文档要求compact 100/h, full 200/h) |
| 审计链按session_id分链 | ✅ | `_get_chain_lock(session_id)` 实现per-session锁 |
| 错误码体系 | ✅ | E1008/E1010/E4007/E4005等已定义 |
| 数据保留策略 | ✅ | 门诊15年/住院30年/操作5年/审计30年配置正确 |
| JWT RS256签名 | ✅ | `jwt_algorithm='RS256'`，使用HSM签名 |
| JWT 120天轮换周期 | ❌ | 未实现自动轮换定时器(P2-5) |
| JWT 7天并行验证期 | ✅ | 密钥轮换保留旧密钥机制支持并行验证 |

---

## 四、代码质量审查结果

| 审查项 | 状态 | 说明 |
|--------|------|------|
| HSM操作线程安全 | ✅ | `_lock = threading.RLock()`，单例+锁保护 |
| 脱敏引擎并发安全 | ✅ | `_rules_lock = threading.RLock()`, `_token_lock = threading.Lock()` |
| 审计链写入并发安全 | ✅ | `_get_chain_lock(session_id)` per-session asyncio.Lock |
| Token存储并发安全 | ✅ | `_lock = threading.RLock()` |
| 私钥导出禁止 | ✅ | `get_private_key()` 直接抛出 `RuntimeError` |
| 无硬编码密钥 | ✅ | 生产环境强制 `HSM_KEY_ENCRYPTION_KEY` 环境变量 |
| API Key哈希存储 | ✅ | 测试验证SHA256哈希存储 |
| 日志无明文敏感信息 | ✅ | Token日志仅显示前8位+`...` |
| HSM签名P99<5ms | ⚠️ | 性能基准测试存在但E2E测试失败，需修复后重新验证 |
| 脱敏P99<5ms | ✅ | 性能基准测试通过 |
| 同意撤回<500ms | ❌ | 测试失败(P0-4) |

---

## 五、改进建议优先级排序

| 优先级 | 问题编号 | 改进建议 | 预估工作量 |
|--------|----------|----------|-----------|
| P0 | P0-2 | 修复SafetyGateway层测试传入consent_status参数 | 0.5h |
| P0 | P0-3 | 修复HSM降级事件机制与测试对齐 | 2h |
| P0 | P0-4 | 修复同意撤回延迟测试，优化撤回处理链路 | 4h |
| P0 | P0-5 | 修复E2E HSM签名测试 | 2h |
| P0 | P0-6 | 修复E2E Token轮换测试 | 2h |
| P1 | P1-1 | 修复SafetyGateway检查器测试 | 3h |
| P1 | P1-2 | 修复三层脱敏all_passed判断逻辑 | 1h |
| P1 | P1-3 | 修复应急签名激活条件测试 | 1h |
| P1 | P1-4 | 补充DICOM存储配置项 | 1h |
| P1 | P1-5 | 对齐SG同意缓存TTL配置 | 0.5h |
| P1 | P1-6 | 修复患者姓名嵌入脱敏规则 | 1h |
| P2 | P2-1 | 补充56条测试达到CI≥1000目标 | 4h |
| P2 | P2-4 | 实现compact模式60天密钥轮换差异化 | 1h |
| P2 | P2-5 | 实现JWT密钥120天自动轮换检查 | 2h |
| P2 | P2-6 | HSM降级阈值移至配置项 | 1h |
| P2 | P2-7 | 修复asyncio事件循环DeprecationWarning | 1h |

---

## 六、自反思记录

| 维度 | 反思 |
|------|------|
| 审查深度 | 本次审查覆盖了全部SEC实现代码和测试文件，但受限于Python环境配置问题，部分E2E测试未能完整运行 |
| 发现质量 | P0问题均为实际测试失败，非理论推断，可信度高 |
| 遗漏风险 | SafetyGateway微服务端代码未在本次审查范围内（仅审查了客户端），需单独审查 |
| 改进方向 | 下次审查应先确保Python环境可用，再执行完整测试套件 |
