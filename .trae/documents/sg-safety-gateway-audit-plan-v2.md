# SG安全网关轨道系统性审查计划（第二轮）

## 审查背景

本计划基于第一轮审查的修复成果，对当前代码状态进行二次系统性审查。第一轮已修复的P0问题包括：
- FIX-13: `SafetyGatewayUnavailableException` NameError → 已改为`SGServiceUnavailableError`
- FIX-17: `ConsentStatus.PENDING` 未处理 → 已添加PENDING=DENIED逻辑
- FIX-20: `check_person_name()` 逻辑缺陷 → 已改用`finditer()+group(0)`
- FIX-25/26: E4005B/E4005C从未抛出 → 已在`checker.py`中添加raise路径
- FIX-05: Redis→PG降级缺失 → 已在`consent_checker.py`中添加PG直接查询
- FIX-06/07: COMBINED_FAILURE/EDGE_DISCONNECTED/SG_UNAVAILABLE_CONSENT_WITHDRAWN无法触发 → 已添加触发路径
- FIX-12/14/15: 错误/模型重复定义 → 已统一到`errors.py`+`threshold.py`
- FIX-16: CORS计算逻辑 → 已简化为硬编码`localhost:8000`
- FIX-18: FAILED+degraded_mode矛盾 → E4005A场景已设`degraded_mode=False`
- FIX-21: 两阶段校验 → 已添加`consent_status_at_input`快速拒绝
- FIX-22: DB超时+Redis未命中返回DENIED → 已改为返回`None`
- FIX-08/38/39: Docker Compose配置 → Redis oom=-500, SG内存=512M, start_period=5s
- FIX-09/36: 内存预算配置化/密码默认值 → 已添加`sg_memory_budget_mb`, 密码改为CHANGEME

---

## 第二轮审查发现 — 仍需修复的问题

### P0-Critical（运行时错误/安全漏洞）

#### REMAIN-FIX-01: `errors.py`的`ERROR_CODE_MAP`缺少E4005B/E4005C映射
- **文件**: [errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/errors.py#L162)
- **问题**: `DatabaseUnavailableError`和`CombinedUnavailableError`已定义但未加入`ERROR_CODE_MAP`
- **影响**: `get_error_class_by_code("E4005B")`返回`SafetyGatewayError`而非`DatabaseUnavailableError`，错误码映射失效
- **修复**: 在`ERROR_CODE_MAP`中添加`"E4005B": DatabaseUnavailableError, "E4005C": CombinedUnavailableError`

#### REMAIN-FIX-02: `checker.py`中`is_db_error`分支后的WITHDRAWN检查存在死代码
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L311-L339)
- **问题**: 第276行`is_db_error=True`时直接raise异常，永远不会到达第311行的`WITHDRAWN`检查。因此`SG_UNAVAILABLE_CONSENT_WITHDRAWN`降级原因在`is_db_error=True`时无法触发——它被E4005B/E4005C异常抢先了
- **影响**: `SG_UNAVAILABLE_CONSENT_WITHDRAWN`降级原因仅在`is_timeout=True`（非`is_db_error`）且consent=WITHDRAWN时才可能触发，但当前代码中`is_timeout`和`is_db_error`互斥，且timeout路径在第292行已提前返回
- **修复**: 需重新设计`is_db_error`+WITHDRAWN的组合处理逻辑——当DB错误且consent=WITHDRAWN时，应优先返回E4005A而非E4005B

#### REMAIN-FIX-03: `validation_result.py`从`threshold.py`导入`ThresholdCheckResult`产生循环导入风险
- **文件**: [validation_result.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/validation_result.py#L11)
- **问题**: `validation_result.py`从`threshold.py`导入`ThresholdCheckResult`，而`threshold.py`可能间接依赖`validation_result.py`的其他类型（如`DegradedReason`），形成循环依赖
- **修复**: 将`ThresholdCheckResult`提取到独立的`sg_models_common.py`或直接在`validation_result.py`中定义（原设计如此）

#### REMAIN-FIX-04: `main.py`中`consent_status_at_input`使用`ConsentStatus()`构造可能抛异常
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L264)
- **问题**: `ConsentStatus(consent_status_at_input)` — 如果客户端传入非法字符串（如"unknown"），`ConsentStatus("unknown")`会抛`ValueError`
- **修复**: 添加try-except或使用`ConsentStatus._value2member_map_.get()`安全查找

### P1-High（功能缺陷/设计偏差）

#### REMAIN-FIX-05: 测试`test_perform_full_check_db_error`期望`SG_UNAVAILABLE`但代码现在raise异常
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L589-L594)
- **问题**: 第一轮修复FIX-25将`is_db_error`路径从返回`CheckResult(SG_UNAVAILABLE)`改为`raise SGDatabaseUnavailableError`，但测试仍期望`result.status == CheckResultStatus.SG_UNAVAILABLE`
- **影响**: 该测试现在会抛出未捕获的`SGDatabaseUnavailableError`，测试失败
- **修复**: 更新测试为`with pytest.raises(SGDatabaseUnavailableError)`

#### REMAIN-FIX-06: 测试`test_sg_returns_e4005_when_db_unavailable`同样期望`SG_UNAVAILABLE`
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L1452-L1461)
- **问题**: 同REMAIN-FIX-05，mock返回`(None, False, True)`但现在代码raise而非返回
- **修复**: 更新测试

#### REMAIN-FIX-07: 测试`test_sg_returns_e4005_on_circuit_breaker_open`同样期望`SG_UNAVAILABLE`
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L1464-L1470)
- **问题**: 同REMAIN-FIX-05
- **修复**: 更新测试

#### REMAIN-FIX-08: 测试`test_sg_continues_operation_after_main_service_recovery`前3次期望`SG_UNAVAILABLE`
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L1473-L1498)
- **问题**: 同REMAIN-FIX-05，前3次mock返回`(None, False, True)`会raise
- **修复**: 更新测试

#### REMAIN-FIX-09: `main.py`的`/health`端点未根据状态返回不同HTTP状态码
- **文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L169-L173)
- **问题**: 所有健康状态（healthy/degraded/unhealthy）都返回HTTP 200，主服务无法通过HTTP状态码快速判断SG是否可用
- **设计文档要求**: 设计文档定义了3次连续失败=不可用的机制，主服务依赖HTTP状态码做判断
- **修复**: healthy→200, degraded→200(带warning header), unhealthy→503

#### REMAIN-FIX-10: `health.py`中Redis unhealthy导致overall=unhealthy，但设计文档要求compact模式不检查Redis
- **文件**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L33-L39)
- **问题**: `check_all()`中Redis unhealthy直接导致overall=unhealthy，但00b-统一配置参考手册明确指出"compact模式readiness probe不检查Redis连接"
- **修复**: 添加`env`配置判断，compact模式下Redis unhealthy降级为degraded而非unhealthy

#### REMAIN-FIX-11: 白名单字段名与设计文档不一致
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L32-L42), [sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L16-L21)
- **问题**: 代码使用`anatomical_position`/`echo_characteristics`/`boundary_characteristics`等，设计文档(02-核心流程与知识库.md)使用`anatomical_location`/`echogenicity`/`boundary`等
- **判断**: 需确认实际数据模型字段名——如果代码中的字段名是实际API使用的，则文档需更新；反之代码需修改
- **修复**: 与数据模型对齐，建议保留代码中的更详细命名（更好的可读性），更新设计文档

#### REMAIN-FIX-12: `FORBIDDEN_GRADING_TERMS`仅过滤顶层字符串值
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L598-L608)
- **问题**: 降级输出中分级词汇过滤仅检查`isinstance(value, str)`的顶层值，嵌套在dict/list中的分级词汇不会被过滤
- **修复**: 递归检查所有字符串值

#### REMAIN-FIX-13: 缺少7种降级场景的完整端到端测试
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L1010-L1056)
- **问题**: `TestDegradedPathScenarios`仅3个测试（熔断器打开、阈值降级、缓存TTL），设计文档Gate 1要求7种故障注入场景
- **缺失场景**: Redis不可用→PG轮询、边缘断连、组合故障、SG不可用→全部拒绝、脱敏降级+SG不可用、SG不可用+患者撤回同意
- **修复**: 添加6个降级场景测试

#### REMAIN-FIX-14: 缺少`_rules_sync_subscriber()`规则热更新测试
- **文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L119-L136)
- **问题**: 规则同步订阅功能无测试覆盖
- **修复**: 添加Redis Pub/Sub触发规则重载的测试

#### REMAIN-FIX-15: `consent_checker.py`中`_query_consent_status`无记录时返回`DENIED`
- **文件**: [consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L89-L90)
- **问题**: 当数据库中无同意记录时，`_query_consent_status`返回`ConsentStatus.DENIED`。但设计文档04-安全合规.md要求"无记录时应视为PENDING（待确认）"，PENDING已在checker中正确处理为拒绝
- **修复**: 将`return ConsentStatus.DENIED`改为`return ConsentStatus.PENDING`

### P2-Medium（代码质量/测试质量）

#### REMAIN-FIX-16: `HealthChecker._latency_samples`并发安全
- **文件**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L174-L177)
- **问题**: `record_latency()`和`compute_latency_stats()`操作`_latency_samples`列表无锁保护，高并发下可能数据竞争
- **修复**: 使用`collections.deque(maxlen=500)`替代list+pop(0)，或添加`threading.Lock`

#### REMAIN-FIX-17: `TestDegradedPathScenarios.test_checker_with_circuit_breaker_open`测试副作用
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L1016-L1031)
- **问题**: 向全局`circuit_breaker_manager`添加"sg_db_consent"熔断器并打开，teardown未清理
- **修复**: 添加teardown_method清理

#### REMAIN-FIX-18: `test_full_check_execution_order`中mock了`threshold_engine.check_async`返回`ThresholdCheckResult`
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L1369-L1376)
- **问题**: 从`validation_result`导入`ThresholdCheckResult`，但实际`ThresholdCheckResult`定义在`threshold.py`中。由于第一轮修复将`validation_result.py`改为从`threshold.py`导入，此处导入路径可能不一致
- **修复**: 确认导入路径正确

#### REMAIN-FIX-19: `sg_config.py`中`allowed_origins`配置项未被使用
- **文件**: [sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L45)
- **问题**: `allowed_origins`配置项存在但`main.py`中CORS配置已硬编码，配置项无效
- **修复**: 移除配置项或让CORS使用配置值

#### REMAIN-FIX-20: `health.py`中`_start_time`为ClassVar导致多实例共享
- **文件**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L21)
- **问题**: `_start_time: ClassVar[datetime] = datetime.now(timezone.utc)` — ClassVar在类定义时求值，所有实例共享同一启动时间。如果HealthChecker在测试中被重新创建，uptime计算不准确
- **修复**: 改为实例变量`self._start_time = datetime.now(timezone.utc)`

---

## 审查执行计划

### 步骤1：修复P0运行时错误（4项）
1. REMAIN-FIX-01: `ERROR_CODE_MAP`添加E4005B/E4005C
2. REMAIN-FIX-02: 重新设计`is_db_error`+WITHDRAWN组合逻辑
3. REMAIN-FIX-03: 解决`ThresholdCheckResult`循环导入风险
4. REMAIN-FIX-04: `ConsentStatus()`构造安全化

### 步骤2：修复P1功能缺陷（11项）
1. REMAIN-FIX-05~08: 更新4个因E4005B/E4005C变更而失效的测试
2. REMAIN-FIX-09: `/health`端点返回语义化HTTP状态码
3. REMAIN-FIX-10: compact模式Redis健康检查降级
4. REMAIN-FIX-11: 白名单字段名对齐（确认后更新文档或代码）
5. REMAIN-FIX-12: `FORBIDDEN_GRADING_TERMS`递归过滤
6. REMAIN-FIX-13: 添加6个降级场景测试
7. REMAIN-FIX-14: 添加规则热更新测试
8. REMAIN-FIX-15: 无同意记录返回PENDING而非DENIED

### 步骤3：修复P2代码质量问题（5项）
1. REMAIN-FIX-16: 延迟采样并发安全
2. REMAIN-FIX-17: 熔断器测试teardown清理
3. REMAIN-FIX-18: 确认ThresholdCheckResult导入路径
4. REMAIN-FIX-19: 清理无效`allowed_origins`配置
5. REMAIN-FIX-20: `_start_time`改为实例变量

### 步骤4：验证与回归测试
1. 运行全部单元测试确认无回归
2. 运行性能基准测试确认P99<5ms
3. 运行lint和typecheck

---

## 审查发现汇总表

| 编号 | 严重程度 | 类别 | 修复项描述 |
|------|----------|------|-----------|
| REMAIN-FIX-01 | P0 | 代码 | ERROR_CODE_MAP缺少E4005B/E4005C映射 |
| REMAIN-FIX-02 | P0 | 功能 | is_db_error+WITHDRAWN组合逻辑死代码 |
| REMAIN-FIX-03 | P0 | 代码 | ThresholdCheckResult循环导入风险 |
| REMAIN-FIX-04 | P0 | 功能 | ConsentStatus()构造可能抛ValueError |
| REMAIN-FIX-05 | P1 | 测试 | test_db_error期望SG_UNAVAILABLE但代码raise |
| REMAIN-FIX-06 | P1 | 测试 | test_e4005_when_db_unavailable期望SG_UNAVAILABLE |
| REMAIN-FIX-07 | P1 | 测试 | test_e4005_on_circuit_breaker_open期望SG_UNAVAILABLE |
| REMAIN-FIX-08 | P1 | 测试 | test_recovery前3次期望SG_UNAVAILABLE |
| REMAIN-FIX-09 | P1 | 功能 | /health端点未返回语义化HTTP状态码 |
| REMAIN-FIX-10 | P1 | 功能 | compact模式Redis unhealthy不应导致overall=unhealthy |
| REMAIN-FIX-11 | P1 | 功能 | 白名单字段名与设计文档不一致 |
| REMAIN-FIX-12 | P1 | 功能 | FORBIDDEN_GRADING_TERMS仅过滤顶层字符串 |
| REMAIN-FIX-13 | P1 | 测试 | 缺少6个降级场景端到端测试 |
| REMAIN-FIX-14 | P1 | 测试 | 缺少规则热更新测试 |
| REMAIN-FIX-15 | P1 | 功能 | 无同意记录应返回PENDING而非DENIED |
| REMAIN-FIX-16 | P2 | 代码 | 延迟采样并发安全 |
| REMAIN-FIX-17 | P2 | 测试 | 熔断器测试teardown未清理 |
| REMAIN-FIX-18 | P2 | 测试 | ThresholdCheckResult导入路径确认 |
| REMAIN-FIX-19 | P2 | 配置 | allowed_origins配置项未被使用 |
| REMAIN-FIX-20 | P2 | 代码 | _start_time为ClassVar导致多实例共享 |

**统计**: P0=4项, P1=11项, P2=5项, 共计20项修复项

---

## 与第一轮审查的对照

| 维度 | 第一轮发现 | 已修复 | 第二轮新发现 | 说明 |
|------|-----------|--------|-------------|------|
| 运行时错误 | 8项P0 | 6项 | 4项P0 | 第一轮修复引入了新的测试失效和逻辑问题 |
| 功能缺陷 | 16项P1 | 11项 | 11项P1 | 第一轮修复改变了错误处理路径，需更新测试 |
| 代码质量 | 15项P2 | 10项 | 5项P2 | 剩余为并发安全、配置清理等 |
| **合计** | **39项** | **27项** | **20项** | 第二轮重点：测试对齐+逻辑完善 |

### 第一轮修复引入的回归问题
第一轮将`is_db_error`路径从返回`CheckResult`改为`raise Exception`，这是一个正确的架构改进（E4005B/C应作为异常传播），但未同步更新4个相关测试，导致测试会失败。这是第二轮最紧急的修复项。
