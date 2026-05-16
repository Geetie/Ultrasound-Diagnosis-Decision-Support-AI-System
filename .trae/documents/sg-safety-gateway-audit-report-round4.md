# SG安全网关轨道系统性审查报告（第四轮）

**审查日期**：2026-05-10  
**审查范围**：SafetyGateway轨道全部源码、测试、配置与部署文件  
**审查原则**：独立、客观、深入、批判性  
**前置参考**：第三轮审查报告（sg-safety-gateway-audit-report-round3.md）

---

## 一、审查范围与方法

### 1.1 审查范围

| 类别 | 文件数 | 行数 | 审查深度 |
|------|--------|------|----------|
| 核心源码 `safety_gateway/` | 17 | ~3,500 | 逐文件静态分析 + 执行路径追踪 |
| 客户端与集成层 | 6 | ~800 | 关键路径审查 |
| 测试文件 | 7 | ~3,200 | 覆盖率 + 质量审查 |
| 配置与部署 | 7 | ~200 | 一致性审查 |

### 1.2 审查方法

1. **第三轮30项修复落地验证**：逐项代码级确认
2. **静态代码分析**：运行时崩溃风险、并发安全、资源泄漏、性能瓶颈
3. **执行路径追踪**：四阶段校验流程、7种降级场景、同意状态校验
4. **测试逻辑审查**：覆盖率、Mock正确性、隔离性、边界条件
5. **文档一致性比对**：对照5份核心设计文档

---

## 二、第三轮30项修复落地验证

### 验证结果汇总

| 修复项 | 描述 | 状态 | 验证说明 |
|--------|------|------|----------|
| SG-FIX-01 | /health result["overall"]键名Bug | ✅ FIXED | main.py已使用`result["status"]` |
| SG-FIX-02 | get_start_time/get_uptime为classmethod | ✅ FIXED | health.py已改为实例方法 |
| SG-FIX-03 | is_db_error=True时WITHDRAWN分支不可达 | ✅ NOT A BUG | Redis缓存降级路径可返回WITHDRAWN状态，分支可达 |
| SG-FIX-04 | is_timeout优先于WITHDRAWN/DENIED检查 | ✅ FIXED | checker.py中WITHDRAWN/DENIED检查在is_timeout之前 |
| SG-FIX-05 | _filter_forbidden_terms仅替换第一个匹配 | ✅ FIXED | 使用`value = value.replace(...)`赋值方式替换全部 |
| SG-FIX-06 | 熔断器OPEN后绕过直接查PG | ✅ FIXED | consent_checker.py熔断器OPEN后仅查Redis缓存 |
| SG-FIX-07 | Redis缓存TTL 60秒过长 | ✅ FIXED | 默认TTL改为10秒(`sg_consent_cache_ttl_seconds=10`) |
| SG-FIX-08 | threshold _lazy_load并发缺陷 | ✅ FIXED | 使用`asyncio.Event`替代布尔标志 |
| SG-FIX-09 | Redis URL替换对db编号>=10错误 | ✅ FIXED | 使用`re.sub(r'/\d+$', '/1', url)` |
| SG-FIX-10 | _persist_threshold_to_db双重commit | ✅ FIXED | 不再显式调用`session.commit()` |
| SG-FIX-11 | _rules_sync_subscriber pubsub资源泄漏 | ✅ FIXED | 使用`async with redis_client.pubsub()` |
| SG-FIX-12 | _rules_sync_subscriber异常后不重连 | ✅ FIXED | while True循环 + 指数退避 |
| SG-FIX-13 | reload_rules()无锁保护 | ✅ FIXED | 使用`self._rules_lock` |
| SG-FIX-14 | 默认规则与YAML规则手机号脱敏不同步 | ✅ FIXED | YAML使用`\\1****\\3`与默认规则一致 |
| SG-FIX-15 | 熔断器OPEN后PG查询成功未写Redis | ✅ FIXED | 熔断器OPEN后不再直接查PG（由SG-FIX-06解决） |
| SG-FIX-16 | _retry_query预算检查未含查询耗时 | ✅ FIXED | 包含`self._query_timeout` |
| SG-FIX-17 | _rules_sync_subscriber无测试 | ✅ FIXED | TestRulesSyncSubscriber类3个测试方法 |
| SG-FIX-18 | 白名单字段名与设计文档不一致 | ✅ FIXED | 字段名已更新为设计文档规范 |
| SG-FIX-19 | E4005C无测试覆盖 | ✅ FIXED | TestSGCombinedUnavailableError类1个测试 |
| SG-FIX-20 | 快速拒绝路径遗漏pending状态 | ✅ FIXED | `!= "granted"`覆盖所有非授权状态 |
| SG-FIX-21 | 熔断器测试teardown清理不完整 | ✅ FIXED | 3处teardown_method已添加 |
| SG-FIX-22 | 异步方法mock缺少AsyncMock | ✅ FIXED | 17+处使用`new_callable=AsyncMock` |
| SG-FIX-23 | get_safety_gateway_checker非线程安全 | ✅ FIXED | 双重检查锁定模式 |
| SG-FIX-24 | CORS配置硬编码 | ✅ FIXED | 可配置化`sg_cors_allowed_origins` |
| SG-FIX-25 | Windows内存监控返回0.0 | ✅ FIXED | 三重降级 + 返回-1.0 + 日志告警 |
| SG-FIX-26 | compact模式降级掩盖非Redis故障 | ✅ FIXED | 仅当Redis为唯一unhealthy组件时降级 |
| SG-FIX-27 | 21个SG配置项未在00b文档登记 | ❌ NOT FIXED | 26/31项仍未文档化（84%缺失） |
| SG-FIX-28 | SG_DEPLOYMENT_MODE命名不一致 | ✅ FIXED | 统一使用`deployment_profile` |
| SG-FIX-29 | _filter_forbidden_terms无递归深度限制 | ✅ FIXED | depth参数 + MAX_RECURSION_DEPTH检查 |
| SG-FIX-30 | desensitization_degraded参数未使用 | ✅ FIXED | checker.py已移除该参数 |

**修复率**：29/30 已修复（96.7%），1项文档问题未修复

---

## 三、新发现的问题

### P0级（生产运行时崩溃风险）

#### SG-FIX-R4-01：threshold.py Core Select返回错误数据类型

**文件**：[threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py)  
**严重程度**：P0 — 生产环境运行时崩溃  
**影响范围**：阈值动态加载与持久化

**问题描述**：

`load_thresholds_from_db()` 和 `_persist_threshold_to_db()` 使用 `OrganDiseaseThreshold.__table__.select()` 进行Core级查询，然后调用 `result.scalars().all()` 或 `result.scalar_one_or_none()`。在SQLAlchemy 2.0中，Core Select的 `scalars()` 返回第一列的值（organ字符串），而非ORM对象。

**受影响代码**：

```python
# load_thresholds_from_db
result = await session.execute(
    OrganDiseaseThreshold.__table__.select()
)
db_thresholds = result.scalars().all()  # 返回 [str, str, ...] 而非 [OrganDiseaseThreshold, ...]
for threshold in db_thresholds:
    key = threshold.organ  # AttributeError: 'str' object has no attribute 'organ'

# _persist_threshold_to_db
result = await session.execute(
    OrganDiseaseThreshold.__table__.select()
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type == disease_type)
)
existing = result.scalar_one_or_none()  # 返回 str 而非 ORM 对象
if existing:
    existing.confidence_threshold = threshold  # AttributeError: 'str' object has no attribute 'confidence_threshold'
```

**修复建议**：

```python
# load_thresholds_from_db: 使用ORM select
from sqlalchemy import select
result = await session.execute(
    select(OrganDiseaseThreshold)
)
db_thresholds = result.scalars().all()

# _persist_threshold_to_db: 使用ORM select
result = await session.execute(
    select(OrganDiseaseThreshold)
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type == disease_type)
)
existing = result.scalar_one_or_none()
```

**当前为何未暴露**：测试中未调用`load_thresholds_from_db()`连接真实数据库，该路径在空表场景下不触发。

---

#### SG-FIX-R4-02：ThreeLayerDesensitization进程内调用SG违反进程隔离

**文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py)  
**严重程度**：P0 — 架构设计违反  
**影响范围**：进程隔离、安全底线

**问题描述**：

`ThreeLayerDesensitizationManager` 的第三层直接导入并调用 `safety_gateway.checker.get_safety_gateway_checker()` 和 `safety_gateway.desensitization.sg_desensitization_engine`，属于进程内调用。这违反了01-系统架构设计.md中"独立操作系统进程，通过HTTP localhost通信"的要求。

**设计文档要求**：
> SafetyGateway为独立操作系统进程（独立Python package safety_gateway/，独立uvicorn实例，通过HTTP localhost通信）

**当前行为**：
- `three_layer_desensitization.py` 直接 `from safety_gateway.checker import get_safety_gateway_checker`
- 主服务崩溃时，进程内SG checker同步崩溃，无法独立返回E4005
- Gate 0验证项"主服务崩溃时独立返回E4005"在此路径下无法满足

**修复建议**：
- 第三层应通过 `SafetyGatewayClient`（HTTP）调用SG服务，而非直接导入
- 或明确三层脱敏为进程内预处理，最终安全校验仍由独立进程SG执行

---

### P1级（功能缺陷/安全隐患）

#### SG-FIX-R4-03：desensitize()方法未使用_rules_lock保护

**文件**：[desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py)  
**严重程度**：P1 — 并发数据不一致  
**影响范围**：脱敏校验结果一致性

**问题描述**：

`check_sensitive_data()` 方法在读取 `_rules` 和 `_compiled_patterns` 时使用了 `self._rules_lock`，但 `desensitize()` 方法直接读取 `self._rules` 和 `self._compiled_patterns` 而不加锁。在规则热更新期间，`desensitize()` 可能读取到不一致的规则和编译模式组合。

**修复建议**：在 `desensitize()` 中对规则和模式读取加锁，或使用快照模式（在锁内复制引用后释放锁）。

---

#### SG-FIX-R4-04：FORBIDDEN_GRADING_TERMS为set导致迭代顺序不确定

**文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py)  
**严重程度**：P1 — 结果不确定性  
**影响范围**：禁忌词过滤的一致性

**问题描述**：

`FORBIDDEN_GRADING_TERMS` 声明为 `ClassVar[set[str]]`，Python set的迭代顺序不确定。在 `_filter_forbidden_terms()` 中遍历该集合时，不同运行可能产生不同的过滤结果。例如，若两个禁用词有重叠子串，先匹配哪个会影响最终结果。

**修复建议**：改为 `ClassVar[tuple[str, ...]]` 并按长度降序排列（长词优先匹配），确保确定性。

---

#### SG-FIX-R4-05：safety_gateway_client.py check_contraindication失败时返回fail-open

**文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py)  
**严重程度**：P1 — 安全策略违反  
**影响范围**：禁忌校验安全性

**问题描述**：

`check_contraindication()` 方法在超时或异常时返回 `{"has_contraindication": False, ...}`，即"无禁忌"。这是fail-open模式，对于安全关键系统是危险的——如果SG不可用，禁忌校验应默认"有禁忌"（fail-closed）。

**修复建议**：异常时返回 `{"has_contraindication": True, "contraindications": [], "message": "禁忌校验不可用，默认存在禁忌"}` 或抛出异常由调用方决定。

---

#### SG-FIX-R4-06：_degraded_response为死代码

**文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py)  
**严重程度**：P1 — 死代码  
**影响范围**：代码可维护性

**问题描述**：

`_degraded_response()` 方法（第193-204行）定义了降级响应逻辑，但没有任何调用点。该方法根据 `settings.safety_gateway_enable_degraded_mode` 决定降级时是否放行，但当前代码直接使用 `_e4005_response()` 硬性阻断。

**修复建议**：要么移除死代码，要么在适当位置集成（如短暂不可用时使用降级响应而非E4005）。

---

#### SG-FIX-R4-07：desensitization_degraded参数客户端发送但服务端不接收

**文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py)  
**严重程度**：P1 — 死代码/数据丢失  
**影响范围**：SG校验策略

**问题描述**：

`SafetyGatewayClient.check_output()` 发送 `desensitization_degraded` 参数（第44/59行），但SG端的 `SafetyCheckRequest` 模型不包含该字段。Pydantic v2默认忽略额外字段，导致该信息被静默丢弃。

**修复建议**：要么在 `SafetyCheckRequest` 中添加该字段并在校验逻辑中使用，要么从客户端移除。

---

### P2级（代码质量/文档缺失）

#### SG-FIX-R4-08：compact模式redis_status["impact"]设置逻辑不一致

**文件**：[health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py)  
**严重程度**：P2 — 逻辑不一致  
**影响范围**：健康检查报告准确性

**问题描述**：

当compact模式下Redis不可用时，`redis_status["impact"] = "degraded"` 被无条件设置，即使其他组件也不健康（此时overall为"unhealthy"）。这导致健康检查报告中Redis的影响级别与实际严重程度不符。

**修复建议**：将 `redis_status["impact"] = "degraded"` 移到 `if not other_unhealthy:` 分支内。

---

#### SG-FIX-R4-09：httpx超时硬编码为3.0秒

**文件**：[safety_gateway_client.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/services/safety_gateway_client.py)  
**严重程度**：P2 — 可配置性  
**影响范围**：客户端超时行为

**问题描述**：

`_get_client()` 中 `httpx.AsyncClient(timeout=3.0)` 硬编码超时为3秒，不可配置。在P99<5ms的设计要求下，3秒超时远超需求，但缺乏灵活性。

**修复建议**：将超时提取为配置项 `sg_http_client_timeout_seconds`。

---

#### SG-FIX-R4-10：26/31 SG配置项未在00b文档登记

**文件**：[00b-统一配置参考手册.md](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/docs-for-guiding%20generating%20docs/超声诊疗决策支持系统/00b-统一配置参考手册.md)  
**严重程度**：P2 — 文档缺失  
**影响范围**：运维可维护性

**问题描述**：

sg_config.py中31个配置项仅5个（16%）在00b文档中有记录。缺失项包括数据库连接池、Redis连接池、脱敏规则路径、重试策略、熔断器参数、性能监控阈值、CORS、同意缓存等关键配置。

**未文档化配置项清单**（26项）：
- 数据库：database_url, db_query_timeout, db_pool_size, db_max_overflow, db_pool_timeout
- Redis：redis_url, redis_pool_max_connections, redis_pool_timeout
- SG核心：safety_gateway_whitelist_fields, safety_gateway_enable_degraded_mode
- 脱敏规则：desensitization_rules_safety_path, desensitization_rules_safety_patch_path, medical_person_names_path
- 重试：sg_db_retry_max_attempts, sg_db_retry_delay_base, sg_db_retry_max_total_time_seconds
- 熔断器：sg_circuit_breaker_failure_threshold, sg_circuit_breaker_success_threshold, sg_circuit_breaker_cooldown_period
- 性能：sg_p99_latency_warning_threshold_ms, sg_p99_latency_critical_threshold_ms, sg_memory_budget_mb
- 其他：sg_cors_allowed_origins, sg_consent_cache_ttl_seconds, sg_threshold_cache_ttl_seconds, env

**额外发现**：文档中引用了 `SAFETY_GATEWAY_HEALTH_CHECK_INTERVAL` 和 `SAFETY_GATEWAY_HEALTH_CHECK_TIMEOUT` 但sg_config.py中不存在这两个配置项。

---

## 四、与参考文档的偏差分析

### 4.1 P0文档偏差：01-系统架构设计.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 进程隔离 | 独立进程，HTTP localhost通信 | three_layer_desensitization.py进程内调用SG | **P0** |
| 代码组织 | 不导入融合层模块 | safety_gateway/未导入融合层 ✅ | 无偏差 |
| Redis连接 | 独立连接池(DB 1) | SafetyGatewayRedisPool使用DB 1 ✅ | 无偏差 |
| NER方案 | 纯正则，不依赖ML NER | 使用正则+医疗人名词典 ✅ | 无偏差 |
| 同意状态校验 | 直查PG，禁用缓存 | 正常路径直查PG，降级路径查Redis缓存 ✅ | 无偏差 |
| OOM保护 | oom_score_adj=-1000 | 启动脚本+Docker Compose均配置 ✅ | 无偏差 |
| A级安全标准 | 先校验后输出，零容忍 | checker.py四阶段校验 ✅ | 无偏差 |
| 降级策略 | SG不可用→E4005 | checker + client均实现 ✅ | 无偏差 |
| 内存预算 | ≤512MB | health.py监控 + sg_memory_budget_mb配置 ✅ | 无偏差 |

### 4.2 P0文档偏差：04-安全合规.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 三重脱敏防线 | SG为第三道防线 | three_layer_desensitization.py实现三层 ✅ | 无偏差 |
| 规则异构冗余 | 独立编写规则集 | desensitization_rules_safety.yaml + patch.yaml ✅ | 无偏差 |
| 双重降级保障 | 双重缺失时拒绝输出 | E4005A错误码实现 ✅ | 无偏差 |
| 患者同意管理 | 两阶段校验 | 入口快速拒绝 + 输出前直查PG ✅ | 无偏差 |

### 4.3 P1文档偏差：06-开发环境与进度规划.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 代码量 | 2500-3000行 | ~3500行（超出16%） | P2（超出可接受） |
| 测试用例 | 80-120个 | 202个（超出68%） | P2（超出可接受） |
| 端口配置 | SAFETY_GATEWAY_PORT=9001 | 默认9001 ✅ | 无偏差 |
| Gate 0 | 主服务崩溃时独立返回E4005 | 进程内调用路径不满足 | **P0** |
| 7种降级场景 | 全部通过 | TestDegradedPathSevenScenarios 7个测试 ✅ | 无偏差 |
| Docker Compose | depends_on + 健康检查 | 已配置 ✅ | 无偏差 |

### 4.4 P2文档偏差：02-核心流程与知识库.md + 00b-统一配置参考手册.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 白名单字段名 | 与设计文档一致 | 已更新 ✅ | 无偏差 |
| 配置项文档化 | 全部登记 | 26/31未登记（84%缺失） | **P2** |
| 配置约束关系 | 排队超时5s~15s | sg_validator.py校验 ✅ | 无偏差 |

---

## 五、SafetyGateway轨道修复项显式声明

以下修复项**明确属于SafetyGateway轨道**，需由SG轨道负责修复：

### P0级修复项（必须修复，影响生产运行）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R4-01** | threshold.py Core Select返回错误数据类型，生产环境必崩 | `safety_gateway/threshold.py` | 代码修复 |
| **SG-FIX-R4-02** | ThreeLayerDesensitization进程内调用SG违反进程隔离 | `app/security/three_layer_desensitization.py` | 架构修复 |

### P1级修复项（应当修复，存在安全隐患）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R4-03** | desensitize()未使用_rules_lock保护 | `safety_gateway/desensitization.py` | 代码修复 |
| **SG-FIX-R4-04** | FORBIDDEN_GRADING_TERMS为set迭代顺序不确定 | `safety_gateway/checker.py` | 代码修复 |
| **SG-FIX-R4-05** | check_contraindication失败时fail-open | `app/services/safety_gateway_client.py` | 代码修复 |
| **SG-FIX-R4-06** | _degraded_response死代码 | `app/services/safety_gateway_client.py` | 代码清理 |
| **SG-FIX-R4-07** | desensitization_degraded参数发送但不接收 | `safety_gateway/main.py` + `app/services/safety_gateway_client.py` | 代码修复 |

### P2级修复项（建议修复，提升质量）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R4-08** | compact模式redis_status["impact"]设置不一致 | `safety_gateway/health.py` | 代码修复 |
| **SG-FIX-R4-09** | httpx超时硬编码3.0秒 | `app/services/safety_gateway_client.py` | 配置化 |
| **SG-FIX-R4-10** | 26/31 SG配置项未在00b文档登记 | `00b-统一配置参考手册.md` | 文档补充 |

### 历史遗留未修复项

| 修复项 | 描述 | 状态 |
|--------|------|------|
| **SG-FIX-27** | 21个SG配置项未在00b文档登记 | ❌ 仍未修复（已扩展为SG-FIX-R4-10） |

---

## 六、测试逻辑审查结果

### 6.1 测试覆盖率

| 指标 | 要求 | 实际 | 评估 |
|------|------|------|------|
| 测试用例数 | 80-120 | 202 | ✅ 超出要求 |
| 降级场景覆盖 | 7种 | 7种 | ✅ 完整 |
| E4005系列错误码 | E4005/A/B/C | 全部有测试 | ✅ 完整 |
| 进程隔离测试 | Gate 0 | TestServiceIsolation + TestMainServiceCrashScenario | ✅ 存在 |
| P99延迟基准 | <5ms | TestSafetyGatewayPerformance | ✅ 存在 |
| OOM保护测试 | oom_score_adj | TestOOMProtectionScenario 5个测试 | ✅ 存在 |
| 规则热更新测试 | reload + rollback | TestDesensitizationRuleReloadWithRollback | ✅ 存在 |
| _rules_sync_subscriber | 订阅+重载+取消 | TestRulesSyncSubscriber 3个测试 | ✅ 已修复 |

### 6.2 测试质量评估

| 维度 | 评估 | 说明 |
|------|------|------|
| Mock正确性 | ✅ 良好 | 17+处使用`new_callable=AsyncMock` |
| 测试隔离性 | ✅ 良好 | 3处teardown_method清理全局状态 |
| 边界条件 | ✅ 良好 | TestBoundaryConditions + TestBoundaryConditionsExtended |
| 参数化覆盖 | ✅ 良好 | 脱敏引擎使用@pytest.mark.parametrize |

### 6.3 测试盲区

| 盲区 | 说明 | 风险 |
|------|------|------|
| load_thresholds_from_db无真实DB测试 | 测试使用内存默认阈值，未验证DB查询路径 | **高** — SG-FIX-R4-01未被测试发现 |
| ThreeLayerDesensitization进程内调用 | 未测试进程隔离场景 | **高** — SG-FIX-R4-02未被测试发现 |
| check_contraindication fail-open | 未测试客户端禁忌校验失败时的默认返回 | **中** — SG-FIX-R4-05未被测试发现 |

---

## 七、安全合规关键项审查

| 审查项 | 结果 | 说明 |
|--------|------|------|
| 患者撤回同意后立即拒绝所有输出 | ✅ 通过 | WITHDRAWN→FAILED，零容忍 |
| SG不可用时拒绝所有诊断输出 | ⚠️ 部分通过 | HTTP路径✅，进程内调用路径❌ |
| 降级输出仅保留白名单字段 | ✅ 通过 | apply_degraded_output正确过滤 |
| Redis缓存TTL在安全窗口内 | ✅ 通过 | 默认10秒 |
| 熔断器OPEN后不再直接查PG | ✅ 通过 | 仅查Redis缓存 |
| 禁忌校验fail-closed | ❌ 不通过 | check_contraindication返回fail-open |
| 进程隔离 | ❌ 不通过 | 三层脱敏进程内调用 |

---

## 八、审查结论

### 8.1 总体评估

SafetyGateway轨道在第三轮审查后修复了29/30项问题（96.7%修复率），代码质量显著提升。本轮审查发现10项新问题，其中2项P0级、5项P1级、3项P2级。

### 8.2 关键风险

1. **SG-FIX-R4-01**（P0）：threshold.py的Core Select查询将在生产环境数据库有记录时导致运行时崩溃。该Bug因测试未覆盖真实DB路径而长期潜伏。

2. **SG-FIX-R4-02**（P0）：ThreeLayerDesensitization进程内调用SG违反了架构设计的进程隔离要求，使得Gate 0验证项在三层脱敏路径下无法满足。

3. **SG-FIX-R4-05**（P1）：check_contraindication的fail-open行为违反了安全系统应默认拒绝的原则。

### 8.3 修复优先级建议

1. **立即修复**：SG-FIX-R4-01（运行时崩溃）、SG-FIX-R4-02（架构违反）
2. **本迭代修复**：SG-FIX-R4-03~R4-07（功能缺陷/安全隐患）
3. **下迭代修复**：SG-FIX-R4-08~R4-10（代码质量/文档）

### 8.4 自我反思

| 维度 | 反思 |
|------|------|
| 第三轮审查遗漏 | SG-FIX-R4-01（Core Select数据类型）在第三轮未被捕获，原因是审查聚焦于逻辑正确性而非SQLAlchemy API使用正确性。未来审查应增加ORM查询模式专项检查。 |
| 测试盲区 | 3个P0/P1问题未被测试发现，说明测试策略偏重Mock测试而缺乏集成测试。建议增加真实DB连接的集成测试。 |
| 架构偏差 | 进程内调用SG是设计偏差，说明三层脱敏模块开发时未严格遵循进程隔离约束。建议在CI中增加架构约束检查（如import依赖分析）。 |
