# SG安全网关轨道系统性审查报告（第十轮）

**审查日期**：2026-05-12
**审查范围**：SafetyGateway轨道全部源码、测试、配置、部署及文档一致性
**审查原则**：独立、客观、深入、批判性
**审查方法**：逐文件静态代码分析 + 运行时可达性验证 + 设计文档交叉验证 + 降级路径端到端追踪

---

## 一、审查概况

| 维度 | 数值 |
|------|------|
| 核心代码文件 | 18个.py文件，约4,290行 |
| 设计文档预估 | 2,500-3,000行 |
| 超出预估 | +43%~+72% |
| 测试用例 | 245个（238单元+7性能） |
| 设计文档预估 | 80-120个 |
| 超出预估 | +104%~+206% |
| 降级场景枚举 | 10种DegradedReason |
| 设计文档要求 | 7种 |
| E4005子类型 | E4005/E4005A1/E4005A2/E4005B/E4005C |
| R9修复项验证 | 6项已修复，3项部分修复，5项文档/统计类 |

---

## 二、R9遗留修复项验证

| R9编号 | 问题描述 | 验证结果 | 详细分析 |
|--------|----------|----------|----------|
| SG-FIX-R9-01 | consent_checker Redis降级路径 | ✅已修复 | consent_checker.py已完全移除Redis缓存路径，直查PG，PG不可用时返回ConsentCheckResult(None, False, True)。模块文档字符串已更新为"唯一路径：直查PostgreSQL" |
| SG-FIX-R9-02 | desensitization_degraded语义错误 | ✅已修复 | three_layer_desensitization.py第200行传递`layer2_degraded`（第2层降级状态），第193-195行注释明确说明语义修正 |
| SG-FIX-R9-03 | docker-compose缺少restart策略 | ✅已修复 | docker-compose.dev.yml第98行已添加`restart: always` |
| SG-FIX-R9-04 | 键名双轨制 | ✅已修复 | three_layer_desensitization.py第133行使用`degraded_mode`，第212-213行使用`passed`/`degraded_mode`。safety_gateway_client.py第319行使用`passed`，第322行使用`degraded_mode`。兼容取值已移除 |
| SG-FIX-R9-05 | COMBINED_FAILURE标记语义 | ✅已修复 | degraded_output.py第127-130行：`fusion_bypassed=True`+`safety_gateway_unavailable=False`+`combined_failure=True` |
| SG-FIX-R9-06 | SENSITIVE_DATA_DETECTED标记语义 | ✅已修复 | degraded_output.py第107-110行使用`sg_output_degraded=True`+`fusion_degraded=False` |
| SG-FIX-R9-07 | sg_patient_name_simple规则disabled | ⚠️部分修复 | YAML中`sg_patient_name_simple`仍为`enabled: false`，但`sg_patient_name_standalone`已改为`enabled: true`（YAML第42行）。医疗人名词典匹配提供兜底覆盖。无前缀姓名正则检测缺口仍存在 |
| SG-FIX-R9-08 | Redis缓存TTL=60s | ⚠️部分修复 | consent_checker已移除Redis缓存路径，但sg_config.py第49行`sg_consent_cache_ttl_seconds=10`仍存在（死配置，无代码引用） |
| SG-FIX-R9-09 | OOM保护Windows检测 | ✅已修复 | health.py第368-374行已添加Windows环境检测和note说明："OOM protection not verifiable on Windows — must be verified in Docker/Linux deployment" |
| SG-FIX-R9-10 | 代码量超出预估 | ⚠️未修复 | 约4,290行，超出预估上限约43%。属于文档更新类，非代码缺陷 |
| SG-FIX-R9-11 | 测试用例超出预估 | ⚠️未修复 | 245个测试用例，超出预估上限约104%。属于正向偏差，更充分的测试覆盖 |
| SG-FIX-R9-12 | circuit_breaker线程安全 | ⚠️部分修复 | circuit_breaker.py第70-84行已添加详细的asyncio单线程约束注释和uvicorn --workers 1约束说明。但_update_state()仍无锁保护，依赖asyncio单线程前提 |
| SG-FIX-R9-13 | redis_pool多事件循环 | ⚠️部分修复 | redis_pool.py第69-84行close()方法已添加事件循环ID检查和日志警告。但旧事件循环的客户端可能未正确关闭 |
| SG-FIX-R9-14 | 端到端P99阈值 | ✅已修复 | sg_config.py第45-46行已添加`sg_e2e_p99_latency_warning_threshold_ms=40.0`和`sg_e2e_p99_latency_critical_threshold_ms=50.0`。health.py第241-286行实现了e2e延迟统计 |

---

## 三、新发现的问题

### P0级（运行时错误/安全关键）

#### SG-FIX-R10-01：main.py /api/v1/consent-status 端点调用不存在的方法

- **严重程度**：P0
- **文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L237)
- **设计文档参考**：01-系统架构设计 §3.4 "SafetyGateway独立进程提供同意状态查询API"
- **问题描述**：第237行 `consent_status, is_timeout, is_db_error = await checker.check_consent_status(patient_id)` 调用`SafetyGatewayChecker`的`check_consent_status`方法，但`SafetyGatewayChecker`类没有此方法。`ConsentChecker.check_consent_status`返回`ConsentCheckResult` NamedTuple（字段：status, timeout, error），而非三元组。此端点在运行时会抛出`AttributeError: 'SafetyGatewayChecker' object has no attribute 'check_consent_status'`。
- **安全风险**：同意状态查询API完全不可用，前端无法通过SG获取患者同意状态。
- **修复建议**：(1) 在`SafetyGatewayChecker`中添加代理方法`check_consent_status`，内部调用`self._consent_checker.check_consent_status(patient_id)`并返回`ConsentCheckResult`；(2) 修改main.py第237行为`result = await checker.check_consent_status(patient_id); consent_status = result.status; is_timeout = result.timeout; is_db_error = result.error`。
- **修复类型**：代码修复

#### SG-FIX-R10-02：main.py /ready 端点引用不存在的键名"checks"

- **严重程度**：P0
- **文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L213)
- **设计文档参考**：06-开发环境与进度规划 "健康检查端点http://localhost:9001/health"
- **问题描述**：第213行 `result.get("checks", {}).get("database", {}).get("status")` 引用`checks`键，但`HealthChecker.check_all()`返回的结果使用`components`键（第116-124行）。此端点在运行时始终返回503（not_ready），因为`checks`键不存在，导致`core_ok`始终为False。
- **安全风险**：Kubernetes/Docker就绪探针始终判定SG未就绪，可能导致：(1) 主服务永远无法启动（depends_on condition: service_healthy）；(2) 负载均衡器永远将SG标记为不可用。
- **修复建议**：将第213行改为 `result.get("components", {}).get("database", {}).get("status") == "healthy" and result.get("components", {}).get("rules_engine", {}).get("status") == "healthy"`。
- **修复类型**：代码修复

#### SG-FIX-R10-03：checker.py _check_consent_phase中E4005A2路径不可达

- **严重程度**：P0
- **文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L396-L409)
- **设计文档参考**：01-系统架构设计 E4005A2 "安全校验不可用且患者同意状态异常（已撤回），拒绝输出"
- **问题描述**：第396-397行 `if ctx.is_db_error: if ctx.consent_status == ConsentStatus.WITHDRAWN:` 但当`is_db_error=True`时，`ConsentChecker.check_consent_status`返回`ConsentCheckResult(None, False, True)`，即`consent_status=None`。因此`ctx.consent_status == ConsentStatus.WITHDRAWN`永远为False，E4005A2路径不可达。
- **安全风险**：当数据库不可用且患者已撤回同意时，应触发E4005A2（最严格拒绝），但实际触发E4005B（数据库不可用），降低了安全防护等级。E4005A2的错误消息"安全校验不可用且患者同意状态异常（已撤回），拒绝输出"永远不会被返回。
- **修复建议**：E4005A2场景需要结合入口阶段传入的`consent_status_at_input`判断。当DB不可用且入口阶段同意状态为"withdrawn"时，应触发E4005A2。修改逻辑为：`if ctx.is_db_error and consent_status_at_input == "withdrawn":` 或在_check_consent_phase方法签名中添加入口同意状态参数。
- **修复类型**：代码修复

### P1级（功能偏差/语义错误）

#### SG-FIX-R10-04：checker.py E4005B错误消息仍引用"Redis缓存未命中"

- **严重程度**：P1
- **文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L418)
- **设计文档参考**：01-系统架构设计 §3.4 "SafetyGateway直查PG，不使用缓存"
- **问题描述**：第418行 `raise SGDatabaseUnavailableError(error_message="数据库查询异常且Redis缓存未命中，无法获取同意状态（E4005B）")` 但consent_checker已移除Redis缓存降级路径，"Redis缓存未命中"描述已过时且与当前实现不一致。
- **修复建议**：更新错误消息为"数据库查询异常，无法获取同意状态（E4005B）"。
- **修复类型**：代码修复

#### SG-FIX-R10-05：errors.py DatabaseUnavailableError文档字符串仍引用"Redis缓存未命中"

- **严重程度**：P1
- **文件**：[errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/errors.py#L170)
- **设计文档参考**：01-系统架构设计 §3.4
- **问题描述**：第170行 `DatabaseUnavailableError` 的文档字符串为"数据库不可用，Redis缓存未命中"，与R9-01修复（移除Redis缓存路径）不一致。
- **修复建议**：更新文档字符串为"数据库不可用，无法获取同意状态"。
- **修复类型**：代码修复

#### SG-FIX-R10-06：sg_config.py sg_consent_cache_ttl_seconds为死配置

- **严重程度**：P1
- **文件**：[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L49)
- **设计文档参考**：01-系统架构设计 §3.4 "SafetyGateway禁用缓存"
- **问题描述**：第49行 `sg_consent_cache_ttl_seconds: int = 10` 配置项在consent_checker移除Redis缓存路径后已无任何代码引用，属于死配置。保留此配置可能误导开发者以为SG仍使用Redis缓存，与设计文档"SafetyGateway禁用缓存"的明确要求矛盾。
- **修复建议**：移除此配置项，或添加注释`# DEPRECATED: consent_checker不再使用Redis缓存，此配置已废弃`。
- **修复类型**：代码修复

#### SG-FIX-R10-07：main.py降级输出未传递fusion_available参数

- **严重程度**：P1
- **文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L293)
- **设计文档参考**：01-系统架构设计 降级策略总表
- **问题描述**：第293行 `checker.apply_degraded_output(request.output_data, reason=reason)` 未传递`fusion_available`参数。`apply_degraded_output`的`fusion_available`参数默认为`True`，但当降级原因为`FUSION_UNAVAILABLE`或`COMBINED_FAILURE`时，融合层实际不可用。这导致`degraded_output.py`中`SG_UNAVAILABLE_CONSENT_WITHDRAWN`分支（第149行）的`fusion_degraded`计算可能不正确。
- **修复建议**：从`HealthChecker`获取当前融合层可用性状态，传递给`apply_degraded_output`。修改为`checker.apply_degraded_output(request.output_data, reason=reason, fusion_available=effective_fusion_available)`，其中`effective_fusion_available`需从checker的PhaseContext或HealthChecker获取。
- **修复类型**：代码修复

#### SG-FIX-R10-08：degraded_output.py FUSION_UNAVAILABLE_SENSITIVE_DETECTED分支safety_gateway_unavailable=True语义不正确

- **严重程度**：P1
- **文件**：[degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L135-L140)
- **设计文档参考**：01-系统架构设计 降级策略总表
- **问题描述**：第138行，`FUSION_UNAVAILABLE_SENSITIVE_DETECTED`场景设置`safety_gateway_unavailable=True`。但此场景中SG本身是可用的（正在执行校验并返回FAILED+E4005A1），不可用的是融合层。R9-05修复了COMBINED_FAILURE的同类问题（改为`safety_gateway_unavailable=False`），但遗漏了此分支。
- **安全风险**：前端可能根据`safety_gateway_unavailable=True`显示"安全校验服务不可用"的错误提示，但实际SG正在正常工作（检测到融合层不可用+敏感数据并正确拒绝输出）。用户可能误以为系统安全防线已完全失效。
- **修复建议**：将第138行`safety_gateway_unavailable=True`改为`safety_gateway_unavailable=False`，并添加`fusion_unavailable=True`标记。
- **修复类型**：代码修复

#### SG-FIX-R10-09：sg_validator.py缺少e2e P99阈值校验规则

- **严重程度**：P1
- **文件**：[sg_validator.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_validator.py)
- **设计文档参考**：01-系统架构设计 §3.4 "端到端处理延迟P99须<50ms"
- **问题描述**：sg_validator.py第36-43行校验了`sg_p99_latency_warning_threshold_ms`和`sg_p99_latency_critical_threshold_ms`，但未校验R9-14新增的`sg_e2e_p99_latency_warning_threshold_ms`和`sg_e2e_p99_latency_critical_threshold_ms`。缺少对e2e阈值的合理性校验（如warning < critical）。
- **修复建议**：添加e2e阈值的校验规则，与现有P99阈值校验逻辑对称。
- **修复类型**：代码修复

### P2级（代码质量/风格）

#### SG-FIX-R10-10：代码量约4,290行超出设计文档预估2,500-3,000行约43%-72%

- **严重程度**：P2
- **文件**：safety_gateway/ 全部18个文件
- **设计文档参考**：06-开发环境与进度规划 "独立Python package代码量约2,500-3,000行"
- **问题描述**：实际代码约4,290行，超出预估上限约43%。主要贡献文件：checker.py(795行)、threshold.py(600行)、desensitization.py(490行)、health.py(467行)、main.py(419行)。threshold.py和health.py的代码量较大，包含热更新/订阅/持久化逻辑。
- **修复建议**：评估是否需要精简代码。如果功能完整性需要此代码量，应更新设计文档中的预估。
- **修复类型**：文档更新

#### SG-FIX-R10-11：测试用例245个超出设计文档预估80-120个约104%-206%

- **严重程度**：P2
- **文件**：tests/unit/test_safety_gateway.py(238个) + tests/benchmark/test_safety_gateway_performance.py(7个)
- **设计文档参考**：06-开发环境与进度规划 "测试用例约80-120个"
- **问题描述**：测试用例数量远超预估，但这是正向偏差——更充分的测试覆盖。
- **修复建议**：更新设计文档中的测试用例预估。
- **修复类型**：文档更新

#### SG-FIX-R10-12：redis_pool.py health_check中使用未定义的类变量_consecutive_failures

- **严重程度**：P2
- **文件**：[redis_pool.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/redis_pool.py#L91-L108)
- **问题描述**：第91行 `cls._consecutive_failures = 0` 和第94行 `cls._consecutive_failures = getattr(cls, '_consecutive_failures', 0) + 1` 使用了未在类定义中声明的类变量`_consecutive_failures`。虽然`getattr`的默认值处理了首次访问，但`cls._consecutive_failures = 0`在第91行直接赋值时，如果`health_check`在`get_client`之前被调用，`_consecutive_failures`属性会被动态添加到类上。这种模式不够清晰。
- **修复建议**：在类定义中显式声明`_consecutive_failures: ClassVar[int] = 0`。
- **修复类型**：代码修复

---

## 四、设计文档一致性审查

### 4.1 P0：01-系统架构设计.md

| 审查项 | 设计要求 | 实现现状 | 偏差 | 修复项 |
|--------|----------|----------|------|--------|
| 进程架构 | 独立uvicorn实例，HTTP localhost通信 | ✅ main.py uvicorn.run()，Dockerfile独立进程 | 无 | - |
| HTTP通信P99<5ms | HTTP通信开销P99<5ms | ✅ benchmark测试存在，keep-alive+连接池 | 无 | - |
| 端到端P99<50ms | 端到端处理延迟P99<50ms | ✅ sg_config.py e2e阈值40/50ms，health.py统计 | ⚠️validator缺少e2e校验 | SG-FIX-R10-09 |
| 核心校验 | 脱敏+禁忌+同意状态+一致性 | ✅ checker.py四阶段校验 | 无 | - |
| E4005体系 | E4005/A1/A2/B/C | ✅ errors.py完整定义 | ❌E4005A2路径不可达 | SG-FIX-R10-03 |
| OOM保护 | oom_score_adj=-1000 | ✅ docker-compose+start脚本+health.py检测 | 无 | - |
| 安全等级A | 先校验后输出，零容忍 | ✅ checker.py四阶段校验→PASSED才输出 | 无 | - |
| 降级路径 | 融合层不可用→仅观察描述+fusion_bypassed | ✅ degraded_output.py白名单过滤 | ⚠️fusion_available参数缺失 | SG-FIX-R10-07 |
| 融合层判定 | 基于进程可达性(HTTP /health) | ✅ health.py get_fusion_availability() | 无 | - |
| NER方案 | 结构化身份模式+医疗人名词典，不依赖ML | ✅ desensitization.py纯正则+词典匹配 | ⚠️无前缀姓名检测缺口 | SG-FIX-R9-07 |
| 内存预算 | ≤512MB | ✅ docker-compose.dev.yml 512M限制+health.py内存检测 | 无 | - |
| SG禁用缓存 | SafetyGateway直查PG，不使用缓存 | ✅ consent_checker.py直查PG | ⚠️死配置+过时消息 | SG-FIX-R10-04/05/06 |
| conclusion_suppressed | 融合层主动抑制时SG跳过diagnostic_conclusion过滤 | ✅ checker.py第517-519行 | 无 | - |
| 幻觉严重 | hallucination_severity=severe时附加安全标记 | ✅ checker.py第373-376行 | 无 | - |
| 规则热更新 | Redis Pub/Sub同步+回滚 | ✅ main.py _rules_sync_subscriber() + desensitization.py reload_rules() | 无 | - |
| 就绪检查 | /ready端点正确反映服务就绪状态 | ❌键名错误导致始终返回503 | 运行时错误 | SG-FIX-R10-02 |
| 同意状态查询 | /api/v1/consent-status端点 | ❌调用不存在的方法 | 运行时错误 | SG-FIX-R10-01 |

### 4.2 P0：04-安全合规.md

| 审查项 | 设计要求 | 实现现状 | 偏差 | 修复项 |
|--------|----------|----------|------|--------|
| 三重防线 | SG为第三道防线：结构化身份模式拦截 | ✅ desensitization.py纯正则+词典 | 无 | - |
| 异构冗余 | desensitization_rules_safety.yaml独立编写 | ✅ YAML规则与主规则源模式不同 | 无 | - |
| 补充规则 | desensitization_rules_safety_patch.yaml独立于主规则源 | ✅ patch.yaml包含护照号/医保号/车牌号/银行卡号 | 无 | - |
| 同意校验 | 两阶段：入口快速拒绝+输出前直查PG | ✅ checker.py入口快速拒绝+consent_checker直查PG | 无 | - |
| SG禁用缓存 | SafetyGateway直查PG，不使用缓存 | ✅ consent_checker.py无Redis缓存路径 | ⚠️过时消息+死配置 | SG-FIX-R10-04/05/06 |
| 类型化脱敏 | [REDACTED:{类型}]格式 | ✅ desensitization.py replacement字段 | 无 | - |
| 双重降级 | fusion_degraded+safety_gateway_unavailable标记 | ⚠️FUSION_UNAVAILABLE_SENSITIVE_DETECTED标记语义错误 | R9遗漏 | SG-FIX-R10-08 |
| E4005A2 | SG不可用+患者撤回同意→拒绝输出 | ❌路径不可达 | 安全关键缺陷 | SG-FIX-R10-03 |

### 4.3 P1：06-开发环境与进度规划.md

| 审查项 | 设计要求 | 实现现状 | 偏差 | 修复项 |
|--------|----------|----------|------|--------|
| 代码组织 | 独立Python package safety_gateway/ | ✅ | 无 | - |
| 代码量 | 约2,500-3,000行 | ❌ 约4,290行 | +43%~+72% | SG-FIX-R10-10 |
| 测试用例 | 约80-120个 | ❌ 245个 | +104%~+206% | SG-FIX-R10-11 |
| 端口 | SAFETY_GATEWAY_PORT=9001 | ✅ sg_config.py默认9001 | 无 | - |
| 健康检查 | http://localhost:9001/health | ✅ main.py /health端点 | 无 | - |
| Gate 0 | 主服务崩溃时独立返回E4005 | ✅ 测试存在 | 需实测验证 | - |
| Docker Compose | depends_on+healthcheck+restart | ✅ docker-compose.dev.yml restart: always | 无 | - |
| OOM保护 | oom_score_adj=-1000 | ✅ docker-compose.dev.yml | 无 | - |

---

## 五、降级场景端到端验证

### 5.1 DegradedReason覆盖矩阵

| # | DegradedReason | 触发路径 | checker.py代码位置 | 预期行为 | 验证结果 |
|---|----------------|----------|-------------------|----------|----------|
| 1 | SENSITIVE_DATA_DETECTED | 融合层可用+脱敏检测到敏感数据 | _check_desensitization_phase第562-579行 | DEGRADED+降级输出 | ✅路径正确，sg_output_degraded标记正确 |
| 2 | DB_TIMEOUT | 同意状态查询超时 | _check_consent_phase第435-449行 | DEGRADED+降级输出 | ✅路径正确 |
| 3 | CONTRAINDICATION_HIT | 检测到禁忌条件 | _check_contraindication_threshold_phase第647-667行 | DEGRADED+降级输出 | ✅路径正确 |
| 4 | CONFIDENCE_BELOW_THRESHOLD | 置信度低于阈值 | _check_contraindication_threshold_phase第669-687行 | DEGRADED+降级输出 | ✅路径正确 |
| 5 | FUSION_UNAVAILABLE | 融合层不可用（无其他故障） | _check_contraindication_threshold_phase第689-703行 | DEGRADED+仅观察描述 | ✅路径正确，但fusion_available参数未传递(SG-FIX-R10-07) |
| 6 | COMBINED_FAILURE | 禁忌+阈值+融合不可用组合 | _check_contraindication_threshold_phase第624-645行 | DEGRADED+组合降级 | ✅路径正确，标记已修正(R9-05) |
| 7 | EDGE_DISCONNECTED | 边缘后端断连 | _check_edge_connectivity第490-504行 | DEGRADED+本地降级 | ✅路径正确 |
| 8 | FUSION_UNAVAILABLE_SENSITIVE_DETECTED | 融合层不可用+敏感数据 | _check_desensitization_phase第526-540行 | FAILED+E4005A1 | ✅路径正确，但safety_gateway_unavailable标记语义错误(SG-FIX-R10-08) |
| 9 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | 融合层脱敏降级+敏感数据 | _check_desensitization_phase第542-560行 | FAILED+E4005A1 | ✅路径正确，desensitization_degraded参数传递链已修正(R9-02) |
| 10 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | SG不可用+患者撤回同意 | _check_consent_phase第396-409行 | FAILED+E4005A2 | ❌路径不可达(SG-FIX-R10-03) |

### 5.2 设计文档7种降级场景覆盖分析

| 设计文档场景 | 对应DegradedReason | 覆盖 | R10备注 |
|-------------|-------------------|------|---------|
| 融合层全部不可用 | FUSION_UNAVAILABLE | ✅ | fusion_available参数未传递 |
| SafetyGateway可用+融合层脱敏不可用 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | ✅ | desensitization_degraded已修正 |
| 脱敏校验服务超时 | SENSITIVE_DATA_DETECTED | ✅ | sg_output_degraded标记正确 |
| 禁忌校验命中 | CONTRAINDICATION_HIT | ✅ | - |
| 置信度低于阈值 | CONFIDENCE_BELOW_THRESHOLD | ✅ | - |
| 数据库不可用 | DB_TIMEOUT | ✅ | - |
| 组合故障 | COMBINED_FAILURE | ✅ | 标记已修正 |

---

## 六、测试逻辑审查

| 审查项 | 结果 | 说明 |
|--------|------|------|
| TL-01 测试覆盖率 | ✅ 245个测试用例，远超80-120个预估 | 238单元+7性能 |
| TL-02 降级路径专项测试 | ✅ TestDegradedPathSevenScenarios类覆盖7种场景 | 10种DegradedReason需验证全覆盖 |
| TL-03 Mock正确性 | ⚠️ 需验证 | R9修复后接口变化可能影响mock |
| TL-04 测试隔离性 | ✅ _reset_safety_gateway_checker()重置单例 | - |
| TL-05 边界条件 | ✅ TestBoundaryConditions类存在 | - |
| TL-06 性能测试 | ✅ test_safety_gateway_performance.py存在 | 7个性能测试，含P99延迟测试 |
| TL-07 集成测试 | ✅ TestHTTPEndToEndLatency类存在 | - |
| TL-08 端点可达性测试 | ❌ /ready和/api/v1/consent-status端点缺少测试 | 需补充 |

---

## 七、部署配置审查

| 审查项 | 结果 | 说明 |
|--------|------|------|
| DC-01 docker-compose.dev.yml完整性 | ✅ | restart: always已添加(R9-03) |
| DC-02 Dockerfile正确性 | ✅ | 独立构建，依赖安装正确 |
| DC-03 supervisor配置 | ✅ | safety-gateway.conf存在，autorestart=true |
| DC-04 start_safety_gateway.sh | ✅ | OOM保护设置正确 |
| DC-05 主服务depends_on | ✅ | api服务depends_on safety-gateway condition: service_healthy |

---

## 八、规则异构冗余验证

| 审查项 | 结果 | 说明 |
|--------|------|------|
| HR-01 desensitization_rules_safety.yaml与主规则源对比 | ✅ | 每条规则的description中标注了异构差异，正则模式确实不同 |
| HR-02 patch.yaml补充规则独立性 | ✅ | 包含护照号/医保号/车牌号/银行卡号4条补充规则 |
| HR-03 contraindication_rules_safety.yaml | ✅ | 按器官分类+全局规则，条件枚举清晰 |
| HR-04 medical_person_names.yaml | ✅ | 姓氏+职称后缀组合，默认覆盖20个常见姓氏+8个职称 |

---

## 九、消息/文档字符串一致性审查（R10新增）

| 审查项 | 结果 | 说明 | 修复项 |
|--------|------|------|--------|
| MC-01 errors.py文档字符串 | ❌ | DatabaseUnavailableError仍引用"Redis缓存未命中" | SG-FIX-R10-05 |
| MC-02 checker.py错误消息 | ❌ | E4005B消息仍引用"Redis缓存未命中" | SG-FIX-R10-04 |
| MC-03 consent_checker.py模块文档 | ✅ | 已更新为"唯一路径：直查PostgreSQL" | - |
| MC-04 sg_config.py死配置 | ❌ | sg_consent_cache_ttl_seconds无代码引用 | SG-FIX-R10-06 |
| MC-05 日志消息 | ✅ | consent_checker.py日志消息已与实现一致 | - |

---

## 十、修复项汇总

### SafetyGateway轨道应当进行的修复项

| 编号 | 问题描述 | 严重程度 | 修复类型 |
|------|----------|----------|----------|
| SG-FIX-R10-01 | main.py /api/v1/consent-status端点调用不存在的check_consent_status方法 | P0 | 代码修复 |
| SG-FIX-R10-02 | main.py /ready端点引用不存在的键名"checks"，始终返回503 | P0 | 代码修复 |
| SG-FIX-R10-03 | checker.py E4005A2路径不可达（is_db_error=True时consent_status=None） | P0 | 代码修复 |
| SG-FIX-R10-04 | checker.py E4005B错误消息仍引用"Redis缓存未命中" | P1 | 代码修复 |
| SG-FIX-R10-05 | errors.py DatabaseUnavailableError文档字符串仍引用"Redis缓存未命中" | P1 | 代码修复 |
| SG-FIX-R10-06 | sg_config.py sg_consent_cache_ttl_seconds为死配置 | P1 | 代码修复 |
| SG-FIX-R10-07 | main.py降级输出未传递fusion_available参数 | P1 | 代码修复 |
| SG-FIX-R10-08 | degraded_output.py FUSION_UNAVAILABLE_SENSITIVE_DETECTED分支safety_gateway_unavailable=True语义不正确 | P1 | 代码修复 |
| SG-FIX-R10-09 | sg_validator.py缺少e2e P99阈值校验规则 | P1 | 代码修复 |
| SG-FIX-R10-10 | 代码量约4,290行超出设计文档预估2,500-3,000行约43%-72% | P2 | 文档更新 |
| SG-FIX-R10-11 | 测试用例245个超出设计文档预估80-120个约104%-206% | P2 | 文档更新 |
| SG-FIX-R10-12 | redis_pool.py health_check中使用未声明的类变量_consecutive_failures | P2 | 代码修复 |

### 修复优先级排序

**P0（必须立即修复）**：
1. SG-FIX-R10-01：修复/api/v1/consent-status端点——添加代理方法或修改调用方式
2. SG-FIX-R10-02：修复/ready端点——将"checks"改为"components"
3. SG-FIX-R10-03：修复E4005A2路径——结合入口consent_status_at_input判断

**P1（应尽快修复）**：
4. SG-FIX-R10-08：修正FUSION_UNAVAILABLE_SENSITIVE_DETECTED标记语义
5. SG-FIX-R10-07：降级输出传递fusion_available参数
6. SG-FIX-R10-04：更新E4005B错误消息
7. SG-FIX-R10-05：更新DatabaseUnavailableError文档字符串
8. SG-FIX-R10-06：移除或标注sg_consent_cache_ttl_seconds死配置
9. SG-FIX-R10-09：添加e2e P99阈值校验规则

**P2（可延后修复）**：
10. SG-FIX-R10-10：更新设计文档代码量预估
11. SG-FIX-R10-11：更新设计文档测试用例预估
12. SG-FIX-R10-12：声明redis_pool._consecutive_failures类变量

---

## 十一、审查结论

### 11.1 整体评估

SafetyGateway轨道的核心架构设计（进程隔离、四阶段校验、10种降级场景、E4005错误码体系）实现完整，与设计文档的核心要求基本一致。R9修复的6个关键问题（Redis缓存移除、desensitization_degraded语义修正、restart策略、键名统一、降级标记修正）均已正确实施。

### 11.2 关键风险

1. **3个P0级运行时错误**（SG-FIX-R10-01/02/03）是本轮审查最严重的发现：
   - `/api/v1/consent-status`端点会崩溃（AttributeError）
   - `/ready`端点始终返回503（键名错误）
   - E4005A2路径不可达（安全关键路径缺陷）

2. **R9修复遗留问题**：consent_checker移除Redis缓存后，相关错误消息（checker.py第418行）、文档字符串（errors.py第170行）和配置项（sg_config.py第49行）未同步清理，形成"代码已修复但消息/配置未更新"的不一致状态。

3. **降级输出fusion_available参数缺失**（SG-FIX-R10-07）和**FUSION_UNAVAILABLE_SENSITIVE_DETECTED标记语义错误**（SG-FIX-R10-08）是R9修复的遗漏项，影响降级输出的准确性。

### 11.3 正面发现

1. R9的6个核心修复项已正确实施，代码质量明显提升
2. 进程隔离架构完整：独立uvicorn实例、HTTP通信、Docker独立容器、restart: always
3. 四阶段校验流程逻辑清晰，降级场景覆盖全面
4. 规则异构冗余实现正确
5. 健康检查完整：6个组件检查+P99延迟统计+e2e延迟统计+OOM保护检测+内存预算监控
6. 测试覆盖充分：245个测试用例

### 11.4 与R9对比

| 维度 | R9 | R10 | 变化 |
|------|-----|------|------|
| P0级问题 | 3个 | 3个 | R9的3个P0已修复，但发现3个新P0（运行时错误） |
| P1级问题 | 8个 | 6个 | R9的8个P1大部分已修复，发现6个新P1（多为R9遗留） |
| P2级问题 | 3个 | 3个 | 文档更新类+代码质量类 |
| 运行时可达性 | 未审查 | 新增审查维度 | 发现2个运行时崩溃bug |
| 消息一致性 | 未审查 | 新增审查维度 | 发现3个过时消息/配置 |
