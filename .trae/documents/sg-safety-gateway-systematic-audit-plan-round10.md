# SG安全网关轨道系统性审查计划（第十轮）

**审查日期**：2026-05-12
**审查范围**：SafetyGateway轨道全部源码、测试、配置、部署及文档一致性
**审查原则**：独立、客观、深入、批判性
**前置参考**：第九轮审查报告（sg-safety-gateway-audit-report-round9.md）

---

## 一、审查目标

对SG轨道的所有验证环节和测试逻辑进行全面、系统性审查，确保其正确有效性。审查报告将显式声明属于SafetyGateway轨道应当进行的修复项。

本次审查重点：
1. **R9修复项验证**：逐项验证第九轮14个修复项的实际修复状态
2. **新发现的问题**：通过深度代码审查发现R9未覆盖的问题
3. **运行时正确性**：重点关注可能导致运行时错误的代码缺陷
4. **语义一致性**：验证错误消息、配置项、降级标记与实际逻辑的一致性

---

## 二、R9修复项预验证（初步代码审查发现）

基于对当前代码的初步审查，R9修复项状态如下：

| R9编号 | 问题描述 | 初步验证结果 | 详细分析 |
|--------|----------|-------------|----------|
| SG-FIX-R9-01 | consent_checker Redis降级路径 | ✅已修复 | consent_checker.py已移除Redis缓存路径，完全直查PG，PG不可用时返回错误 |
| SG-FIX-R9-02 | desensitization_degraded语义错误 | ✅已修复 | three_layer_desensitization.py第200行传递`layer2_degraded`（第2层降级状态） |
| SG-FIX-R9-03 | docker-compose缺少restart策略 | ✅已修复 | docker-compose.dev.yml第98行已添加`restart: always` |
| SG-FIX-R9-04 | 键名双轨制 | ✅已修复 | 统一为`passed`/`degraded_mode`，兼容取值已移除 |
| SG-FIX-R9-05 | COMBINED_FAILURE标记语义 | ✅已修复 | degraded_output.py第127-130行已修正为`fusion_bypassed=True`+`safety_gateway_unavailable=False` |
| SG-FIX-R9-06 | SENSITIVE_DATA_DETECTED标记语义 | ✅已修复 | degraded_output.py第107-110行使用`sg_output_degraded=True` |
| SG-FIX-R9-07 | sg_patient_name_simple规则disabled | ⚠️待验证 | 需检查YAML和desensitization.py当前状态 |
| SG-FIX-R9-08 | Redis缓存TTL=60s | ✅已修复 | consent_checker已移除Redis缓存路径，但sg_config.py第49行`sg_consent_cache_ttl_seconds=10`仍存在（死配置） |
| SG-FIX-R9-09 | OOM保护Windows检测 | ⚠️待验证 | health.py第368-374行已添加Windows环境note，需确认完整性 |
| SG-FIX-R9-10 | 代码量超出预估 | ⚠️待验证 | 需重新统计 |
| SG-FIX-R9-11 | 测试用例超出预估 | ⚠️待验证 | 需重新统计 |
| SG-FIX-R9-12 | circuit_breaker线程安全 | ⚠️待验证 | 需检查_update_state()当前状态 |
| SG-FIX-R9-13 | redis_pool多事件循环 | ⚠️待验证 | 需检查close()方法当前状态 |
| SG-FIX-R9-14 | 端到端P99阈值 | ✅已修复 | sg_config.py第45-46行已添加e2e阈值配置 |

---

## 三、新发现的问题（初步代码审查）

### P0级（运行时错误/安全关键）

#### SG-FIX-R10-01：main.py /api/v1/consent-status 端点解包ConsentCheckResult方式错误

- **严重程度**：P0
- **文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L237)
- **问题描述**：第237行 `consent_status, is_timeout, is_db_error = await checker.check_consent_status(patient_id)` 调用`SafetyGatewayChecker`的`check_consent_status`方法，但`SafetyGatewayChecker`类没有`check_consent_status`方法。`ConsentChecker.check_consent_status`返回`ConsentCheckResult` NamedTuple（字段：status, timeout, error），而非三元组。此端点在运行时会抛出`AttributeError`。
- **修复建议**：(1) 通过`checker._consent_checker.check_consent_status(patient_id)`调用底层方法，或(2) 在`SafetyGatewayChecker`中添加代理方法。同时将解包改为`result = await ...; consent_status = result.status; is_timeout = result.timeout; is_db_error = result.error`。
- **修复类型**：代码修复

#### SG-FIX-R10-02：main.py /ready 端点引用不存在的键名"checks"

- **严重程度**：P0
- **文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L213)
- **问题描述**：第213行 `result.get("checks", {}).get("database", {}).get("status")` 引用`checks`键，但`HealthChecker.check_all()`返回的结果使用`components`键（而非`checks`）。此端点在运行时始终返回503（not_ready），因为`checks`键不存在，导致`core_ok`始终为False。
- **修复建议**：将`result.get("checks", {})`改为`result.get("components", {})`，并调整子键名（database→database, rules_engine→rules_engine）。
- **修复类型**：代码修复

#### SG-FIX-R10-03：checker.py _check_consent_phase中is_db_error=True时consent_status==WITHDRAWN条件不可达

- **严重程度**：P0
- **文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L396-L409)
- **问题描述**：第396-397行 `if ctx.is_db_error: if ctx.consent_status == ConsentStatus.WITHDRAWN:` 但当`is_db_error=True`时，`ConsentChecker.check_consent_status`返回`ConsentCheckResult(None, False, True)`，即`consent_status=None`。因此`ctx.consent_status == ConsentStatus.WITHDRAWN`永远为False，E4005A2路径（SG不可用+患者撤回同意）不可达。
- **安全风险**：当数据库不可用且患者已撤回同意时，应触发E4005A2拒绝输出，但实际会触发E4005B（数据库不可用），降低了安全防护等级。
- **修复建议**：E4005A2场景需要不同的检测逻辑——在DB不可用前先获取同意状态（如入口快速拒绝阶段已捕获WITHDRAWN），或在DB错误时结合入口阶段传入的`consent_status_at_input`判断。
- **修复类型**：代码修复

### P1级（功能偏差/语义错误）

#### SG-FIX-R10-04：checker.py E4005B错误消息仍引用"Redis缓存未命中"

- **严重程度**：P1
- **文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L418)
- **问题描述**：第418行 `raise SGDatabaseUnavailableError(error_message="数据库查询异常且Redis缓存未命中，无法获取同意状态（E4005B）")` 但consent_checker已移除Redis缓存降级路径，"Redis缓存未命中"描述已过时。
- **修复建议**：更新错误消息为"数据库查询异常，无法获取同意状态（E4005B）"。
- **修复类型**：代码修复

#### SG-FIX-R10-05：errors.py DatabaseUnavailableError文档字符串仍引用"Redis缓存未命中"

- **严重程度**：P1
- **文件**：[errors.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/errors.py#L170)
- **问题描述**：第170行 `DatabaseUnavailableError` 的文档字符串为"数据库不可用，Redis缓存未命中"，与R9-01修复（移除Redis缓存路径）不一致。
- **修复建议**：更新文档字符串为"数据库不可用，无法获取同意状态"。
- **修复类型**：代码修复

#### SG-FIX-R10-06：sg_config.py sg_consent_cache_ttl_seconds为死配置

- **严重程度**：P1
- **文件**：[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L49)
- **问题描述**：第49行 `sg_consent_cache_ttl_seconds: int = 10` 配置项在consent_checker移除Redis缓存路径后已无任何代码引用，属于死配置。保留此配置可能误导开发者以为SG仍使用Redis缓存。
- **修复建议**：移除此配置项，或在注释中明确标注"已废弃：consent_checker不再使用Redis缓存"。
- **修复类型**：代码修复

#### SG-FIX-R10-07：main.py降级输出未传递fusion_available参数

- **严重程度**：P1
- **文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L293)
- **问题描述**：第293行 `checker.apply_degraded_output(request.output_data, reason=reason)` 未传递`fusion_available`参数。`apply_degraded_output`的`fusion_available`参数默认为`True`，但当降级原因为`FUSION_UNAVAILABLE`或`COMBINED_FAILURE`时，融合层实际不可用。这导致`degraded_output.py`中`SG_UNAVAILABLE_CONSENT_WITHDRAWN`分支（第149行）的`fusion_degraded`计算可能不正确。
- **修复建议**：从checker的`_PhaseContext`或`HealthChecker`获取当前融合层可用性状态，传递给`apply_degraded_output`。
- **修复类型**：代码修复

#### SG-FIX-R10-08：degraded_output.py FUSION_UNAVAILABLE_SENSITIVE_DETECTED分支safety_gateway_unavailable=True语义仍不正确

- **严重程度**：P1
- **文件**：[degraded_output.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/degraded_output.py#L135-L140)
- **问题描述**：第138行，`FUSION_UNAVAILABLE_SENSITIVE_DETECTED`场景设置`safety_gateway_unavailable=True`。但此场景中SG本身是可用的（正在执行校验并返回FAILED+E4005A1），不可用的是融合层。`safety_gateway_unavailable=True`暗示SG不可用，与实际不符。R9-05修复了COMBINED_FAILURE的同类问题，但遗漏了此分支。
- **修复建议**：将`safety_gateway_unavailable=True`改为`safety_gateway_unavailable=False`，并添加`fusion_unavailable=True`标记。
- **修复类型**：代码修复

---

## 四、审查范围与维度

### 4.1 代码质量评估

| 审查项 | 审查内容 | 对应文件 |
|--------|----------|----------|
| CQ-01 | 健壮性：空值/None处理、类型安全、异常路径完整性 | 全部18个核心模块 |
| CQ-02 | 可维护性：模块职责单一性、依赖关系清晰度、代码重复度 | safety_gateway/ |
| CQ-03 | 可扩展性：新增规则/器官/降级场景的扩展成本 | desensitization.py, contraindication.py, threshold.py |
| CQ-04 | 性能表现：P99<5ms延迟基准可达性、内存预算≤512MB合规性 | health.py, checker.py, benchmark测试 |
| CQ-05 | 并发安全：asyncio.Lock/threading.Lock使用正确性、单例模式线程安全 | redis_pool.py, checker.py, consent_checker.py |
| CQ-06 | 资源泄漏：数据库连接池、Redis连接池、HTTP连接的生命周期管理 | sg_db.py, redis_pool.py, health.py |
| CQ-07 | 运行时崩溃风险：未捕获异常、递归深度溢出、正则ReDoS | checker.py, desensitization.py |
| CQ-08 | **[R10新增]** 运行时可达性：端点/方法调用链是否可实际执行 | main.py, checker.py |

### 4.2 代码风格合规性

| 审查项 | 审查内容 | 对应文件 |
|--------|----------|----------|
| CS-01 | 命名约定：模块/类/函数/变量命名是否遵循项目统一规范 | safety_gateway/ |
| CS-02 | 类型注解：是否统一使用Python 3.10+风格（X \| Y替代Union） | 全部.py文件 |
| CS-03 | 文档标准：模块/类/关键函数是否有docstring，是否与实现一致 | 全部.py文件 |
| CS-04 | 导入规范：是否遵循标准库/第三方/本地模块分组，无未使用导入 | 全部.py文件 |
| CS-05 | 代码一致性：同类操作是否使用统一模式（如单例获取、错误处理） | safety_gateway/ |
| CS-06 | **[R10新增]** 消息一致性：错误消息/日志/文档字符串是否与当前实现一致 | errors.py, checker.py, consent_checker.py |

### 4.3 功能正确性验证

| 审查项 | 审查内容 | 设计文档参考 |
|--------|----------|-------------|
| FC-01 | 进程隔离：独立uvicorn实例、HTTP localhost通信、主服务崩溃时独立返回E4005 | 01-系统架构设计 §3.4 |
| FC-02 | 核心校验职责：脱敏校验+禁忌校验+同意状态检查+一致性校验四项完整性 | 01-系统架构设计 §3.4 |
| FC-03 | 安全等级A执行：先校验后输出、零容忍策略、未经校验绝对不输出 | 01-系统架构设计 安全分级 |
| FC-04 | OOM保护：oom_score_adj=-1000配置正确性、运行时验证 | 01-系统架构设计 §3.4 + 06-开发环境 |
| FC-05 | 7种降级场景完整性：逐一验证每种DegradedReason的触发路径和输出正确性 | 01-系统架构设计 降级策略总表 |
| FC-06 | P99<5ms延迟基准：HTTP通信开销+端到端处理延迟 | 01-系统架构设计 §3.4 |
| FC-07 | 三重脱敏防线：第三道防线（SG结构化身份模式拦截）实现完整性 | 04-安全合规 §2 |
| FC-08 | 规则异构冗余：desensitization_rules_safety.yaml独立编写、不复制主规则源 | 04-安全合规 §2 |
| FC-09 | 同意状态两阶段校验：入口快速拒绝+输出前直查PostgreSQL | 04-安全合规 §6 |
| FC-10 | 健康检查完整：/health端点、组件级状态、P99延迟统计、OOM状态 | 06-开发环境 |
| FC-11 | Docker Compose配置：depends_on+healthcheck+oom_score_adj+端口9001 | 06-开发环境 |
| FC-12 | E4005错误码体系：E4005/E4005A1/E4005A2/E4005B/E4005C触发路径正确性 | 01-系统架构设计 |
| FC-13 | 降级输出数据完整性：白名单过滤、conclusion_suppressed标记、类型化脱敏 | 01-系统架构设计 降级策略 |
| FC-14 | 融合层可用性判定：基于进程可达性(HTTP /health)而非输出结构 | 01-系统架构设计 §3.4 |
| FC-15 | conclusion_suppressed处理：融合层主动抑制时SG跳过diagnostic_conclusion过滤 | 01-系统架构设计 §3.4 |
| FC-16 | 幻觉严重输出处理：hallucination_severity=severe时附加安全标记 | checker.py |
| FC-17 | 规则热更新：Redis Pub/Sub同步+回滚机制+验证 | 01-系统架构设计 脱敏规则同步 |
| FC-18 | **[R10新增]** 端点可达性验证：所有API端点的调用链是否可实际执行 | main.py |

---

## 五、审查执行步骤

### 步骤1：R9遗留修复项验证（预计30分钟）

逐项验证第九轮审查报告中14个修复项的实际修复状态：

| R9编号 | 描述 | 验证方法 |
|--------|------|----------|
| SG-FIX-R9-01 | consent_checker Redis降级路径 | 代码审查consent_checker.py，确认无Redis缓存路径 |
| SG-FIX-R9-02 | desensitization_degraded语义 | 代码审查three_layer_desensitization.py第200行 |
| SG-FIX-R9-03 | docker-compose restart策略 | 检查docker-compose.dev.yml |
| SG-FIX-R9-04 | 键名双轨制 | 检查three_layer_desensitization.py和safety_gateway_client.py |
| SG-FIX-R9-05 | COMBINED_FAILURE标记 | 检查degraded_output.py |
| SG-FIX-R9-06 | SENSITIVE_DATA_DETECTED标记 | 检查degraded_output.py |
| SG-FIX-R9-07 | sg_patient_name_simple规则 | 检查YAML和desensitization.py |
| SG-FIX-R9-08 | Redis缓存TTL | 检查sg_config.py和consent_checker.py |
| SG-FIX-R9-09 | OOM保护Windows检测 | 检查health.py |
| SG-FIX-R9-10 | 代码量统计 | 重新统计safety_gateway/代码行数 |
| SG-FIX-R9-11 | 测试用例统计 | 重新统计测试用例数量 |
| SG-FIX-R9-12 | circuit_breaker线程安全 | 检查_update_state() |
| SG-FIX-R9-13 | redis_pool多事件循环 | 检查close()方法 |
| SG-FIX-R9-14 | 端到端P99阈值 | 检查sg_config.py和health.py |

**验证标准**：每项需代码级确认修复状态，记录为✅已修复/⚠️部分修复/❌未修复

### 步骤2：运行时可达性审查（R10新增重点）（预计45分钟）

重点审查所有API端点的调用链是否可实际执行，识别运行时错误：

| 序号 | 端点/方法 | 审查重点 |
|------|----------|----------|
| 1 | GET /api/v1/consent-status/{patient_id} | checker.check_consent_status方法是否存在，返回值解包是否正确 |
| 2 | GET /ready | result键名是否与check_all()返回结构匹配 |
| 3 | POST /api/v1/safety/check | 降级输出时fusion_available参数是否正确传递 |
| 4 | GET /health | 健康检查逻辑完整性 |
| 5 | GET /health/watchdog | 看门狗端点逻辑正确性 |
| 6 | POST /api/v1/safety/consistency | 一致性校验端点逻辑 |
| 7 | POST /api/v1/safety/contraindication | 禁忌校验端点逻辑 |

### 步骤3：核心模块逐文件静态分析（预计60分钟）

按以下顺序逐文件审查，重点关注R9修复引入的新问题和遗留问题：

| 序号 | 文件 | 审查重点 |
|------|------|----------|
| 1 | main.py | **[R10重点]** 端点调用链可达性、ConsentCheckResult解包、/ready键名、降级输出fusion_available |
| 2 | checker.py | **[R10重点]** E4005A2不可达路径、E4005B错误消息、_PhaseContext状态管理 |
| 3 | consent_checker.py | R9-01修复验证：确认无Redis缓存路径、直查PG实现完整性 |
| 4 | degraded_output.py | **[R10重点]** FUSION_UNAVAILABLE_SENSITIVE_DETECTED分支标记语义、各降级原因标记一致性 |
| 5 | errors.py | **[R10重点]** DatabaseUnavailableError文档字符串、错误码映射完整性 |
| 6 | desensitization.py | 规则加载/热更新/回滚、线程安全、正则ReDoS风险 |
| 7 | contraindication.py | 规则加载、条件匹配逻辑 |
| 8 | threshold.py | 动态加载、热更新、缓存TTL |
| 9 | health.py | 组件健康检查、P99延迟统计、OOM保护检测、融合层可用性检测 |
| 10 | circuit_breaker.py | 状态机正确性、线程安全 |
| 11 | sg_config.py | **[R10重点]** 死配置项sg_consent_cache_ttl_seconds、配置完整性 |
| 12 | sg_validator.py | 配置校验规则完整性 |
| 13 | sg_db.py | 连接池配置、会话管理 |
| 14 | redis_pool.py | 连接池隔离、多事件循环处理 |
| 15 | sg_models.py | ORM模型与数据库schema一致性 |
| 16 | validation_result.py | 结果模型完整性 |

### 步骤4：降级场景端到端验证（预计45分钟）

逐一验证10种DegradedReason的触发路径，重点关注R10新发现的问题：

| 序号 | DegradedReason | R10关注点 |
|------|----------------|----------|
| 1 | SENSITIVE_DATA_DETECTED | sg_output_degraded标记是否正确传递到客户端 |
| 2 | DB_TIMEOUT | 降级输出中fusion_available参数是否正确 |
| 3 | CONTRAINDICATION_HIT | 降级输出标记正确性 |
| 4 | CONFIDENCE_BELOW_THRESHOLD | 降级输出标记正确性 |
| 5 | FUSION_UNAVAILABLE | fusion_bypassed标记、降级输出fusion_available参数 |
| 6 | COMBINED_FAILURE | fusion_bypassed+combined_failure标记、fusion_available参数 |
| 7 | EDGE_DISCONNECTED | 降级输出标记正确性 |
| 8 | FUSION_UNAVAILABLE_SENSITIVE_DETECTED | **[R10重点]** safety_gateway_unavailable标记语义 |
| 9 | DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED | desensitization_degraded参数传递链完整性 |
| 10 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | **[R10重点]** E4005A2路径是否可达 |

### 步骤5：设计文档一致性审查（预计45分钟）

按优先级对照核心参考文档进行一致性审查：

#### P0：01-系统架构设计.md

| 审查项 | 设计要求 | R10验证重点 |
|--------|----------|-------------|
| 进程架构 | 独立uvicorn实例 | 验证main.py uvicorn.run() |
| P99延迟 | HTTP通信P99<5ms，端到端P99<50ms | 验证e2e阈值配置和health.py统计 |
| 核心校验 | 脱敏+禁忌+同意状态+一致性 | 验证四阶段校验流程 |
| E4005体系 | E4005/A1/A2/B/C | **[R10重点]** 验证E4005A2路径可达性 |
| OOM保护 | oom_score_adj=-1000 | 验证docker-compose+start脚本+health.py |
| SG禁用缓存 | SafetyGateway直查PG | 验证consent_checker无Redis路径 |
| 降级路径 | 7种降级场景 | 验证10种DegradedReason覆盖 |
| 端到端P99 | P99<50ms | 验证sg_config.py e2e阈值配置 |

#### P0：04-安全合规.md

| 审查项 | 设计要求 | R10验证重点 |
|--------|----------|-------------|
| 同意校验 | 两阶段：入口快速拒绝+输出前直查PG | 验证checker.py入口快速拒绝+consent_checker直查PG |
| SG禁用缓存 | SafetyGateway直查PG，不使用缓存 | 验证consent_checker.py无Redis缓存路径 |
| 双重降级标记 | fusion_degraded+safety_gateway_unavailable | **[R10重点]** 验证各降级场景标记语义正确性 |

#### P1：06-开发环境与进度规划.md

| 审查项 | 设计要求 | R10验证重点 |
|--------|----------|-------------|
| 代码量 | 约2500-3000行 | 重新统计 |
| 测试用例 | 约80-120个 | 重新统计 |
| Docker Compose | depends_on+healthcheck+restart | 验证restart: always |
| 端口 | 9001 | 验证 |

### 步骤6：测试逻辑审查（预计30分钟）

| 审查项 | 审查内容 | R10验证重点 |
|--------|----------|-------------|
| TL-01 | 测试覆盖率 | 重新统计测试用例数量 |
| TL-02 | 降级路径专项测试 | 验证10种DegradedReason测试覆盖 |
| TL-03 | Mock正确性 | **[R10重点]** 验证mock是否与R9修复后的接口一致 |
| TL-04 | 测试隔离性 | 检查setup/teardown、单例重置 |
| TL-05 | 边界条件覆盖 | 空值、极值、异常输入 |
| TL-06 | 性能测试有效性 | P99<5ms基准测试 |
| TL-07 | 集成测试覆盖 | HTTP端到端、Docker环境 |
| TL-08 | **[R10新增]** 端点可达性测试 | 验证/ready和/api/v1/consent-status端点是否有测试 |

### 步骤7：部署配置审查（预计20分钟）

| 审查项 | 审查内容 |
|--------|----------|
| DC-01 | docker-compose.dev.yml完整性（含restart: always） |
| DC-02 | Dockerfile.safety-gateway.dev构建正确性 |
| DC-03 | supervisor配置：自动重启≤5s |
| DC-04 | start_safety_gateway.sh：OOM保护设置 |
| DC-05 | 主服务depends_on safety-gateway配置 |

### 步骤8：规则异构冗余验证（预计20分钟）

| 审查项 | 审查内容 |
|--------|----------|
| HR-01 | desensitization_rules_safety.yaml与主规则源正则模式对比 |
| HR-02 | desensitization_rules_safety_patch.yaml补充规则独立性 |
| HR-03 | contraindication_rules_safety.yaml规则完整性 |
| HR-04 | medical_person_names.yaml人名词典质量 |

### 步骤9：消息/文档字符串一致性审查（R10新增）（预计20分钟）

| 审查项 | 审查内容 |
|--------|----------|
| MC-01 | errors.py中所有错误类的文档字符串是否与当前实现一致 |
| MC-02 | checker.py中错误消息是否与当前实现一致（如"Redis缓存未命中"） |
| MC-03 | consent_checker.py模块文档字符串是否与实现一致 |
| MC-04 | sg_config.py中配置项是否有未使用的死配置 |
| MC-05 | 日志消息是否与当前逻辑一致 |

---

## 六、审查执行计划

| 步骤 | 预计耗时 | 产出 |
|------|----------|------|
| 步骤1：R9遗留验证 | 30min | R9修复项验证结果表 |
| 步骤2：运行时可达性审查 | 45min | 运行时错误清单 |
| 步骤3：核心模块静态分析 | 60min | 代码质量问题清单 |
| 步骤4：降级场景端到端验证 | 45min | 降级路径覆盖矩阵 |
| 步骤5：设计文档一致性审查 | 45min | 偏差分析表 |
| 步骤6：测试逻辑审查 | 30min | 测试质量评估 |
| 步骤7：部署配置审查 | 20min | 配置合规性检查 |
| 步骤8：规则异构冗余验证 | 20min | 规则对比结果 |
| 步骤9：消息一致性审查 | 20min | 消息/文档字符串偏差清单 |
| **总计** | **~5小时** | **完整审查报告** |

---

## 七、已知风险与关注点

基于前九轮审查历史和R10初步代码审查，以下为高风险关注项：

1. **E4005A2路径不可达**（SG-FIX-R10-03）：`is_db_error=True`时`consent_status=None`，导致WITHDRAWN判断不可达。这是安全关键路径的缺陷。
2. **/api/v1/consent-status端点运行时错误**（SG-FIX-R10-01）：`SafetyGatewayChecker`没有`check_consent_status`方法，此端点在运行时会崩溃。
3. **/ready端点键名错误**（SG-FIX-R10-02）：引用`checks`而非`components`，导致就绪检查始终失败。
4. **降级输出fusion_available参数缺失**（SG-FIX-R10-07）：降级输出时未传递融合层可用性状态。
5. **FUSION_UNAVAILABLE_SENSITIVE_DETECTED标记语义**（SG-FIX-R10-08）：R9修复了COMBINED_FAILURE的同类问题但遗漏了此分支。
6. **R9修复引入的遗留问题**：consent_checker移除Redis缓存后，相关错误消息和配置项未同步清理。

---

## 八、审查输出格式

审查完成后，将输出以下内容：

1. **审查报告**（sg-safety-gateway-audit-report-round10.md），包含：
   - R9遗留修复项验证结果
   - 新发现的问题（按P0/P1/P2分级）
   - 设计文档偏差分析表
   - 测试覆盖率统计
   - 修复建议与优先级

2. **修复项清单**：显式声明属于SafetyGateway轨道应当进行的修复项，格式为：
   ```
   SG-FIX-R10-{NN}: {问题描述}
   - 严重程度：P0/P1/P2
   - 文件：{文件路径}
   - 设计文档参考：{文档名}§{章节}
   - 修复建议：{具体修复方案}
   - 修复类型：代码修复/配置修复/测试补充/文档更新
   ```
