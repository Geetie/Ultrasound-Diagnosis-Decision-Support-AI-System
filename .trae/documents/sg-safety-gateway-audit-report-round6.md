# SG安全网关轨道系统性审查报告（第六轮）

**审查日期**：2026-05-10
**审查范围**：SafetyGateway轨道全部源码（17文件~3500行）、客户端与集成层（6文件）、测试文件（12文件~3200行）、配置与部署文件（8文件）
**审查原则**：独立、客观、深入、批判性
**前置参考**：第五轮审查报告（sg-safety-gateway-audit-report-round5.md）

---

## 一、审查范围与方法

### 1.1 审查范围

| 类别 | 文件数 | 行数 | 审查深度 |
|------|--------|------|----------|
| 核心源码 `safety_gateway/` | 17 | ~3,500 | 逐文件静态分析 + 执行路径追踪 |
| 客户端与集成层 | 6 | ~800 | 关键路径审查 |
| 测试文件 | 12 | ~3,200 | 覆盖率 + 质量审查 |
| 配置与部署 | 8 | ~200 | 一致性审查 |

### 1.2 审查方法

1. **第五轮9项修复落地验证**：逐项代码级确认
2. **静态代码分析**：运行时崩溃风险、并发安全、资源泄漏、性能瓶颈
3. **执行路径追踪**：四阶段校验流程、10种降级场景、同意状态校验
4. **测试逻辑审查**：覆盖率、Mock正确性、隔离性、边界条件
5. **文档一致性比对**：对照5份核心设计文档
6. **规则异构冗余验证**：逐条对比desensitization_rules_safety.yaml与desensitization_rules.yaml

---

## 二、第五轮遗留修复项验证结果

### 验证结果汇总

| 修复项 | 描述 | 状态 | 验证说明 |
|--------|------|------|----------|
| SG-FIX-R5-01 | threshold.py `_delete_threshold_from_db()`使用Core级delete | ✅ FIXED | 第27行导入`from sqlalchemy import delete, select`，第377-387行使用ORM级`delete(OrganDiseaseThreshold)` |
| SG-FIX-R5-02 | consent_checker.py `_cache_consent_to_redis()`死代码 | ✅ FIXED | consent_checker.py中已无`_cache_consent_to_redis()`方法，正常路径直查PG不写Redis |
| SG-FIX-R5-03 | SafetyCheckRequest.desensitization_degraded接收但未使用 | ✅ FIXED | checker.py第261行`perform_full_check()`接收参数，第442行`_check_desensitization_phase()`使用参数影响校验策略（第468-486行：脱敏降级+敏感数据→FAILED） |
| SG-FIX-R5-04 | safety_gateway_client.py `_degraded_response()`死代码 | ✅ FIXED | client.py中已无`_degraded_response()`方法，仅保留`_e4005_response()` |
| SG-FIX-R5-05 | safety_gateway_client.py使用已弃用asyncio.get_event_loop() | ✅ FIXED | 第46行`asyncio.get_running_loop().time()`，第82行`asyncio.get_running_loop().time()` |
| SG-FIX-R5-06 | sg_db.py get_db_session()自动commit事务语义文档化 | ✅ FIXED | 第28-33行docstring明确说明"yield后自动commit，适用于单操作事务场景" |
| SG-FIX-R5-07 | checker.py apply_degraded_output() list中None值未过滤 | ✅ FIXED | 第710-711行str项`if result is not None: processed.append(result)`，第714-715行`elif item is not None: processed.append(item)` |
| SG-FIX-R5-09 | safety_gateway_client.py类型注解风格不一致 | ✅ FIXED | 使用`dict[str, Any]`、`list[dict[str, Any]]`、`str \| None`等Python 3.10+风格 |
| SG-FIX-R5-11 | health.py OOM保护状态在非Linux环境添加configured_in_compose | ✅ FIXED | 第288行返回`{"status": "unknown", "oom_score_adj": None, "expected": -1000, "configured_in_compose": -1000}` |

**修复率**：9/9 已修复（100%），第五轮所有修复建议均已落地 ✅

---

## 三、新发现的问题

### P1级（功能缺陷/安全隐患）

#### SG-FIX-R6-01：main.py SGError异常未映射HTTP状态码

- **严重程度**：P1 — E4005系列错误返回HTTP 500而非语义化状态码
- **文件**：[main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py) 第268-269行
- **问题描述**：`perform_safety_check()`端点捕获`SGError`后直接re-raise，FastAPI将其作为未处理异常返回HTTP 500 Internal Server Error。当`SGDatabaseUnavailableError`(E4005B)或`SGCombinedUnavailableError`(E4005C)被抛出时，客户端收到的是500而非503或403。客户端通过重试机制最终返回E4005，功能上正确但效率低且丢失了具体子错误码信息。
- **设计文档参考**：01-系统架构设计.md §3.4 E4005错误码子码拆分——E4005A/B/C应有不同的前端提示
- **修复建议**：在FastAPI app中注册`SGError`异常处理器，根据错误子类型映射HTTP状态码：
  - `ConsentSafetyViolationError`(E4005A) → HTTP 503
  - `DatabaseUnavailableError`(E4005B) → HTTP 503
  - `CombinedUnavailableError`(E4005C) → HTTP 503
  - 其他`SGError` → HTTP 500
  同时在响应体中包含`error_code`和`message`字段
- **修复类型**：代码增强

#### SG-FIX-R6-02：contraindication.py和threshold.py单例获取函数无线程安全保护

- **严重程度**：P1 — 并发场景下可能创建多个实例
- **文件**：[contraindication.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/contraindication.py) 第212-215行，[threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py) 第546-549行
- **问题描述**：`get_contraindication_engine()`和`get_threshold_engine()`使用简单全局变量检查，无double-checked locking保护。而`get_safety_gateway_checker()`（checker.py第797-803行）和`get_consent_checker()`（consent_checker.py第196-201行）均使用了`threading.Lock`实现线程安全。虽然当前SG运行在单线程asyncio环境中，但与项目内其他单例模式不一致，且未来扩展时可能引入并发问题。
- **设计文档参考**：01-系统架构设计.md §3.4 SafetyGateway代码组织与依赖隔离——独立Python package应保持内部一致性
- **修复建议**：为`get_contraindication_engine()`和`get_threshold_engine()`添加与`get_safety_gateway_checker()`一致的double-checked locking模式
- **修复类型**：代码修复

#### SG-FIX-R6-03：checker.py `_check_desensitization_phase()`参数类型注解与实际不符

- **严重程度**：P1 — 类型注解错误，影响静态分析工具和IDE提示
- **文件**：[checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py) 第443-444行
- **问题描述**：`_check_desensitization_phase()`的参数声明为`ctx: dict[str, Any]`和`start_time: float`，但实际传入的是`_PhaseContext`实例和`datetime`实例（来自`perform_full_check()`第264行`start_time = datetime.now(timezone.utc)`）。其他阶段方法（`_check_consent_phase`、`_check_contraindication_threshold_phase`、`_check_consistency_phase`）均正确使用`ctx: _PhaseContext`和`start_time: datetime`。
- **设计文档参考**：无直接关联，属代码质量范畴
- **修复建议**：将第443行`ctx: dict[str, Any]`改为`ctx: _PhaseContext`，第444行`start_time: float`改为`start_time: datetime`
- **修复类型**：代码修复

### P2级（代码质量/风格一致性）

#### SG-FIX-R6-04：redis_pool.py使用Python 3.9风格类型注解

- **严重程度**：P2 — 代码风格不一致
- **文件**：[redis_pool.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/redis_pool.py) 第2行、第13行、第49行
- **问题描述**：使用`Optional[redis.Redis]`（第13行）、`Optional[bytes]`（第49行）和`from typing import Optional`（第2行），而safety_gateway/包内其他文件（如checker.py、consent_checker.py）已统一使用`redis.Redis | None`等Python 3.10+风格。
- **修复建议**：移除`from typing import Optional`，改用`redis.Redis | None`和`bytes | None`
- **修复类型**：代码风格

#### SG-FIX-R6-05：sg_config.py和sg_validator.py使用Python 3.9风格类型注解

- **严重程度**：P2 — 代码风格不一致
- **文件**：[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py) 第1行、第15行、第43行；[sg_validator.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_validator.py) 第1行、第6行、第12行
- **问题描述**：使用`List[str]`和`from typing import List`，而包内其他文件已统一使用`list[str]`。
- **修复建议**：移除`from typing import List`，改用`list[str]`
- **修复类型**：代码风格

#### SG-FIX-R6-06：consent_checker.py使用Python 3.9风格类型注解

- **严重程度**：P2 — 代码风格不一致
- **文件**：[consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py) 第12行、第37行
- **问题描述**：`ConsentCheckResult`使用`Optional[ConsentStatus]`，而同文件内`check_consent_status()`返回类型注解使用`ConsentStatus | None`（第143行），风格不一致。
- **修复建议**：将`ConsentCheckResult`的`status`字段改为`ConsentStatus | None`，移除`Optional`导入
- **修复类型**：代码风格

#### SG-FIX-R6-07：three_layer_desensitization.py使用Python 3.9风格类型注解

- **严重程度**：P2 — 代码风格不一致
- **文件**：[three_layer_desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/security/three_layer_desensitization.py) 第9行
- **问题描述**：使用`from typing import Dict, Any, Optional, Tuple`，而同文件第107行已使用`str | None`（Python 3.10+风格），风格混用。
- **修复建议**：统一为Python 3.10+风格：`dict[str, Any]`、`str | None`、`tuple[dict[str, Any], dict[str, Any]]`
- **修复类型**：代码风格

#### SG-FIX-R6-08：__init__.py为空文件，未导出公共API

- **严重程度**：P2 — 模块可发现性差
- **文件**：[__init__.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/__init__.py)
- **问题描述**：`safety_gateway/__init__.py`为空文件（0行），未导出任何公共API。外部使用者需直接`from safety_gateway.checker import ...`，无法通过`from safety_gateway import ...`访问。
- **修复建议**：在`__init__.py`中添加`__all__`和关键导出，如`SafetyGatewayChecker`、`CheckResultStatus`、`DegradedReason`等
- **修复类型**：代码增强

---

## 四、代码质量评估结果

### 4.1 健壮性评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 并发安全 | ✅ 良好 | `_rules_lock`(desensitization.py)、`_sg_checker_lock`(checker.py)、`_lock`(health.py/circuit_breaker.py)覆盖关键共享状态 |
| 资源泄漏 | ✅ 良好 | lifespan中正确关闭Redis连接池、checker、threshold订阅；client.py有close()方法 |
| 异常处理完整性 | ✅ 良好 | PG查询(consent_checker.py)、Redis操作(redis_pool.py)、HTTP调用(client.py)均有try/except |
| 单例模式线程安全 | ⚠️ 部分缺失 | checker/consent_checker使用double-checked locking，contraindication/threshold未使用（SG-FIX-R6-02） |
| 熔断器状态转换 | ✅ 良好 | record_failure/record_success均在_lock保护内 |
| 规则热更新回滚 | ✅ 良好 | reload_rules()失败时恢复old_rules+old_patterns |

### 4.2 可维护性评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 模块职责边界 | ✅ 良好 | checker(协调)/consent_checker(同意)/desensitization(脱敏)/threshold(阈值)/contraindication(禁忌)各司其职 |
| 依赖方向 | ✅ 良好 | safety_gateway/未导入app.或fusion.模块，进程隔离正确 |
| 配置集中度 | ✅ 良好 | 所有可调参数在sg_config.py中，client.py超时从settings读取 |
| 错误类型层次 | ✅ 良好 | errors.py 18种异常类型，继承层次清晰，E4005系列与设计文档对齐 |

### 4.3 可扩展性评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 降级场景扩展 | ✅ 良好 | DegradedReason枚举+apply_degraded_output()分支模式，新增场景只需添加枚举值+处理分支 |
| 规则热更新 | ✅ 良好 | reload_rules()支持回滚，Pub/Sub同步，_validate_rules()校验 |
| 阈值动态配置 | ✅ 良好 | DB加载+Redis订阅，支持器官级和病种级阈值 |

### 4.4 性能评估

| 审查项 | 评估 | 说明 |
|--------|------|------|
| P99延迟基准 | ✅ 良好 | 500样本滑动窗口，warning=4ms/critical=5ms阈值配置正确 |
| HTTP通信开销 | ✅ 良好 | httpx AsyncClient + keep-alive + 连接池(20连接/10 keepalive) |
| PG查询优化 | ✅ 良好 | consent_checker.py单条查询+索引(patient_id, status) |
| Redis连接池隔离 | ✅ 良好 | redis_pool.py第18行强制替换URL为DB 1 |
| 内存预算监控 | ✅ 良好 | health.py多平台内存统计(psutil→/proc→Windows API)，512MB预算+超预算告警 |

---

## 五、代码风格合规性评估结果

### 5.1 类型注解风格

| 文件 | 当前风格 | 评估 |
|------|----------|------|
| checker.py | Python 3.10+ (`str \| None`, `dict[str, Any]`) | ✅ |
| main.py | Python 3.10+ | ✅ |
| desensitization.py | Python 3.10+ | ✅ |
| threshold.py | Python 3.10+ | ✅ |
| health.py | Python 3.10+ | ✅ |
| circuit_breaker.py | Python 3.10+ | ✅ |
| errors.py | Python 3.10+ | ✅ |
| validation_result.py | Python 3.10+ | ✅ |
| sg_types.py | Python 3.10+ | ✅ |
| consent_checker.py | **混合** (`Optional[ConsentStatus]` + `ConsentStatus \| None`) | ⚠️ SG-FIX-R6-06 |
| sg_config.py | **Python 3.9** (`List[str]`) | ⚠️ SG-FIX-R6-05 |
| sg_validator.py | **Python 3.9** (`List[str]`) | ⚠️ SG-FIX-R6-05 |
| redis_pool.py | **Python 3.9** (`Optional[redis.Redis]`) | ⚠️ SG-FIX-R6-04 |
| three_layer_desensitization.py | **混合** (`Dict, Optional, Tuple` + `str \| None`) | ⚠️ SG-FIX-R6-07 |
| safety_gateway_client.py | Python 3.10+ | ✅ (R5-09已修复) |

### 5.2 命名约定

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 类名PascalCase | ✅ | SafetyGatewayChecker, ConsentChecker, ThresholdEngine等 |
| 函数名snake_case | ✅ | perform_full_check, check_consent_status等 |
| 常量UPPER_SNAKE | ✅ | MAX_RECURSION_DEPTH, REDIS_CONSENT_KEY_PREFIX等 |
| 私有方法_前缀 | ✅ | _check_consent_phase, _get_threshold等 |

### 5.3 文档字符串

| 审查项 | 评估 | 说明 |
|--------|------|------|
| 模块级docstring | ✅ | checker.py, consent_checker.py, threshold.py, contraindication.py, errors.py, sg_db.py均有 |
| 类级docstring | ⚠️ 部分 | SafetyGatewayChecker有，ConsentChecker无 |
| 方法级docstring | ⚠️ 部分 | perform_full_check有，check_desensitization无 |

---

## 六、功能正确性验证结果

### 6.1 进程隔离验证（P0）

| 验证项 | 设计要求 | 当前实现 | 结果 |
|--------|----------|----------|------|
| 独立uvicorn实例 | SG以独立进程运行 | main.py第352-360行uvicorn.run独立启动 | ✅ |
| HTTP localhost通信 | 主服务通过HTTP调用SG | client.py通过httpx AsyncClient调用 | ✅ |
| 主服务崩溃时独立存活 | 返回E4005错误码 | docker-compose.dev.yml独立容器 + supervisor看门狗 | ✅ |
| Docker Compose depends_on | api依赖safety-gateway健康检查 | docker-compose.dev.yml第75-76行condition: service_healthy | ✅ |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml第130行 + start_safety_gateway.sh第5行 | ✅ |
| 独立Redis连接池(DB 1) | 与主服务DB 0隔离 | redis_pool.py第18行强制替换URL为/1 | ✅ |

### 6.2 四阶段校验流程验证（P0）

| 阶段 | 设计要求 | 当前实现 | 结果 |
|------|----------|----------|------|
| 阶段1：同意状态校验 | 入口快速拒绝 + 输出前直查PG | checker.py第267-287行(快速拒绝) + 第330-410行(PG直查) | ✅ |
| 阶段2：脱敏校验 | 结构化身份模式拦截 + 医疗人名词典 | checker.py第437-505行 | ✅ |
| 阶段3：禁忌+阈值校验 | 禁忌规则引擎 + 置信度阈值 | checker.py第507-624行 | ✅ |
| 阶段4：一致性校验 | 分级/发现/置信度差异检测 | checker.py第626-659行 | ✅ |
| 执行顺序 | consent→desensitization→contraindication→threshold→consistency | perform_full_check()第291-311行 | ✅ |

### 6.3 降级场景验证（P0）

| 降级场景 | DegradedReason | 代码位置 | 结果 |
|----------|----------------|----------|------|
| 1. 敏感数据检测 | SENSITIVE_DATA_DETECTED | checker.py第494-505行 | ✅ |
| 2. SG不可用 | SG_UNAVAILABLE | client.py超时→E4005 | ✅ |
| 3. DB超时 | DB_TIMEOUT | consent_checker.py第164-170行 | ✅ |
| 4. 禁忌命中 | CONTRAINDICATION_HIT | checker.py第566-586行 | ✅ |
| 5. 置信度低于阈值 | CONFIDENCE_BELOW_THRESHOLD | checker.py第588-606行 | ✅ |
| 6. 融合层不可用 | FUSION_UNAVAILABLE | checker.py第608-622行 | ✅ |
| 7. 组合故障 | COMBINED_FAILURE | checker.py第542-564行 | ✅ |
| 8. 边缘断连 | EDGE_DISCONNECTED | checker.py第412-435行 | ✅ |
| 9. 脱敏降级+SG不可用 | DESENSITIZATION_DEGRADED_SG_UNAVAILABLE | checker.py第468-486行 | ✅ |
| 10. SG不可用+患者撤回 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | checker.py第342-355行 | ✅ |

### 6.4 E4005系列错误码验证（P0）

| 错误码 | 触发条件 | 代码位置 | 结果 |
|--------|----------|----------|------|
| E4005 | SG不可用（客户端通用） | client.py第184-191行 | ✅ |
| E4005A | SG不可用或脱敏降级+敏感数据 | errors.py第144-148行 + checker.py第454-466行 | ✅ |
| E4005B | PG不可用+Redis缓存未命中 | errors.py第150-153行 + checker.py第363-366行 | ✅ |
| E4005C | SG+融合层同时不可用 | errors.py第155-159行 + checker.py第358-362行 | ✅ |

**注意**：E4005B/E4005C在SG服务端抛出时返回HTTP 500（SG-FIX-R6-01），客户端通过重试机制最终返回E4005，功能正确但效率可优化。

### 6.5 安全合规关键项验证（P0）

| 验证项 | 设计要求 | 当前实现 | 结果 |
|--------|----------|----------|------|
| 三重脱敏防线 | SG为第三道防线 | three_layer_desensitization.py第112行通过safety_gateway_client.check_output() HTTP调用SG | ✅ |
| 规则异构冗余 | 独立编写规则集 | desensitization_rules_safety.yaml与desensitization_rules.yaml逐条对比，正则模式、替换策略、规则拆分均存在实质性差异 | ✅ |
| 同意状态直查PG | 正常路径不写Redis缓存 | consent_checker.py无_cache_consent_to_redis方法，正常路径直查PG | ✅ |
| 两阶段同意校验 | 入口快速拒绝 + 输出前直查PG | checker.py第267-287行(consent_status_at_input快速拒绝) + 第330-410行(PG直查) | ✅ |
| A级安全标准 | 先校验后输出，零容忍 | perform_full_check()四阶段全部通过才PASSED，任何阶段失败立即返回 | ✅ |
| 禁忌校验fail-closed | 异常时默认存在禁忌 | client.py第165/168/171行返回has_contraindication=True | ✅ |
| 降级输出仅白名单 | 过滤diagnostic_conclusion，仅保留observations | apply_degraded_output()白名单过滤 + FORBIDDEN_GRADING_TERMS过滤 | ✅ |
| OOM保护 | oom_score_adj=-1000 | docker-compose.dev.yml第130行 + start_safety_gateway.sh第5行 | ✅ |
| consent_status_at_output | SG输出前直查PG获取同意状态快照 | SafetyCheckResponse第40行包含consent_status_at_output字段 | ✅ |
| NER方案 | 纯正则+词典，不依赖ML NER | desensitization.py使用正则+医疗人名词典，无ML依赖 | ✅ |
| 内存预算 | ≤512MB | health.py监控 + sg_memory_budget_mb=512 | ✅ |

### 6.6 边界条件验证（P1）

| 验证项 | 当前处理 | 结果 |
|--------|----------|------|
| 同意状态为None | checker.py第381行：None→DENIED | ✅ |
| 同意状态为非法值 | checker.py第275-277行：ValueError→DENIED | ✅ |
| 递归深度超限 | checker.py第99/119/669行：MAX_RECURSION_DEPTH=10，返回空/原值 | ✅ |
| 规则编译失败 | desensitization.py第203-209行：跳过该规则，记录warning | ✅ |
| 规则重载失败回滚 | desensitization.py第391-394行：恢复old_rules+old_patterns | ✅ |
| 阈值加载失败降级 | threshold.py第171-176行：fallback到默认阈值 | ✅ |
| Redis不可用降级 | consent_checker.py第155-162行：熔断器OPEN→Redis缓存→E4005B | ✅ |
| PG不可用+Redis缓存未命中 | consent_checker.py第161-162行：返回(None, False, True)→E4005B | ✅ |
| 组合故障（≥2项同时） | checker.py第542-543行：active_failures≥2→COMBINED_FAILURE | ✅ |
| 空专科结论列表 | checker.py第168-173行：返回consistent=True | ✅ |
| 白名单字段值为None | checker.py第700-701行：直接赋值None | ✅ |
| 白名单字段值为list | checker.py第706-717行：逐项脱敏+None过滤 | ✅ |

---

## 七、测试逻辑审查结果

### 7.1 测试覆盖率

| 指标 | 要求 | 实际 | 评估 |
|------|------|------|------|
| 测试用例数 | 80-120 | ~202（R5报告） | ✅ 超出要求 |
| 降级场景覆盖 | 7种 | 10种（含3种额外场景） | ✅ 完整 |
| E4005系列错误码 | E4005/A/B/C | 全部有测试 | ✅ 完整 |
| 进程隔离测试 | Gate 0 | TestServiceIsolation + TestMainServiceCrashScenario | ✅ 存在 |
| P99延迟基准 | <5ms | TestSafetyGatewayPerformance | ✅ 存在 |
| OOM保护测试 | oom_score_adj | TestOOMProtectionScenario | ✅ 存在 |
| 规则热更新测试 | reload + rollback | TestDesensitizationRuleReloadWithRollback | ✅ 存在 |
| 四阶段执行顺序 | consent→...→consistency | test_full_check_execution_order | ✅ 存在 |
| 熔断器状态转换 | CLOSED→OPEN→HALF_OPEN→CLOSED | TestCircuitBreaker | ✅ 完整 |

### 7.2 测试质量评估

| 维度 | 评估 | 说明 |
|------|------|------|
| Mock正确性 | ✅ 良好 | 17+处使用`new_callable=AsyncMock`正确模拟异步行为 |
| 测试隔离性 | ✅ 良好 | TestCircuitBreaker有teardown_method清理全局状态 |
| 边界条件 | ✅ 良好 | test_desensitize_none、test_desensitize_empty_string、test_check_unknown_organ等 |
| 参数化覆盖 | ⚠️ 一般 | 脱敏引擎部分使用@pytest.mark.parametrize，但可更广泛 |
| 断言完整性 | ✅ 良好 | 关键输出字段均有断言验证 |

### 7.3 测试盲区

| 盲区 | 风险 | 建议 |
|------|------|------|
| load_thresholds_from_db无真实DB测试 | 中 | 添加集成测试覆盖DB查询路径 |
| _delete_threshold_from_db无测试 | 中 | 添加阈值删除路径测试 |
| 并发请求场景无测试 | 中 | 添加多协程并发测试验证锁正确性 |
| client超时重试完整流程 | 中 | 添加端到端重试+排队超时测试 |
| SGError→HTTP状态码映射 | 低 | 添加API层异常处理器测试（与SG-FIX-R6-01关联） |

---

## 八、与参考文档的偏差分析

### 8.1 P0文档偏差：01-系统架构设计.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 进程隔离 | 独立进程，HTTP localhost通信 | three_layer_desensitization.py通过HTTP调用SG ✅ | 无偏差 |
| 代码组织 | 不导入融合层模块 | safety_gateway/未导入融合层 ✅ | 无偏差 |
| Redis连接 | 独立连接池(DB 1) | SafetyGatewayRedisPool使用DB 1 ✅ | 无偏差 |
| NER方案 | 纯正则，不依赖ML NER | 使用正则+医疗人名词典 ✅ | 无偏差 |
| 同意状态校验 | 直查PG，正常路径禁用缓存 | 正常路径直查PG，不写Redis ✅ | 无偏差 |
| OOM保护 | oom_score_adj=-1000 | 启动脚本+Docker Compose均配置 ✅ | 无偏差 |
| A级安全标准 | 先校验后输出，零容忍 | checker.py四阶段校验 ✅ | 无偏差 |
| 降级策略 | SG不可用→E4005 | checker + client均实现 ✅ | 无偏差 |
| 内存预算 | ≤512MB | health.py监控 + sg_memory_budget_mb配置 ✅ | 无偏差 |
| 健康检查 | 每4s探测，超时1s，3次失败 | Docker Compose配置一致 ✅ | 无偏差 |
| HTTP通信开销P99 | <5ms | warning=4ms, critical=5ms ✅ | 无偏差 |
| 端到端处理延迟P99 | <50ms | 无端到端延迟监控 | **P2** — 仅有HTTP通信开销监控，缺少含内部处理的端到端监控 |
| E4005子码 | E4005A/B/C差异化 | errors.py定义完整，但HTTP响应未区分（SG-FIX-R6-01） | **P1** |

### 8.2 P0文档偏差：04-安全合规.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 三重脱敏防线 | SG为第三道防线 | three_layer_desensitization.py实现三层 ✅ | 无偏差 |
| 规则异构冗余 | 独立编写规则集 | 逐条对比确认实质性差异 ✅ | 无偏差 |
| 双重降级保障 | 双重缺失时拒绝输出 | E4005A错误码实现 ✅ | 无偏差 |
| 患者同意管理 | 两阶段校验 | 入口快速拒绝 + 输出前直查PG ✅ | 无偏差 |
| 禁忌校验fail-closed | 安全系统默认拒绝 | check_contraindication返回fail-closed ✅ | 无偏差 |
| consent_status_at_output | SG输出前直查PG获取同意状态快照 | SafetyCheckResponse包含该字段 ✅ | 无偏差 |

### 8.3 P1文档偏差：06-开发环境与进度规划.md

| 需求项 | 设计要求 | 当前实现 | 偏差等级 |
|--------|----------|----------|----------|
| 代码组织 | 独立Python package safety_gateway/ | 目录结构正确 ✅ | 无偏差 |
| 代码量 | 2500-3000行 | ~3500行（超出17%） | P2（超出可接受） |
| 测试用例数 | 80-120个 | ~202个（超出68%） | P2（超出可接受） |
| 端口配置 | SAFETY_GATEWAY_PORT=9001 | 默认9001 ✅ | 无偏差 |
| Gate 0 | 主服务崩溃时独立返回E4005 | HTTP路径✅，进程内调用路径已修复✅ | 无偏差 |
| Docker Compose | depends_on + 健康检查 | 已配置 ✅ | 无偏差 |
| supervisor看门狗 | 崩溃后自动重启≤5s | safety-gateway.conf配置正确 ✅ | 无偏差 |

---

## 九、SafetyGateway轨道修复项显式声明

以下修复项**明确属于SafetyGateway轨道**，需由SG轨道负责修复：

### P1级修复项（应当修复，存在功能缺陷或安全隐患）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R6-01** | main.py SGError异常未映射HTTP状态码，E4005B/E4005C返回HTTP 500而非503 | `safety_gateway/main.py` | 代码增强 |
| **SG-FIX-R6-02** | contraindication.py和threshold.py单例获取函数无线程安全保护 | `safety_gateway/contraindication.py` + `safety_gateway/threshold.py` | 代码修复 |
| **SG-FIX-R6-03** | checker.py `_check_desensitization_phase()`参数类型注解与实际不符 | `safety_gateway/checker.py` | 代码修复 |

### P2级修复项（建议修复，提升质量）

| 修复项 | 描述 | 文件 | 修复类型 |
|--------|------|------|----------|
| **SG-FIX-R6-04** | redis_pool.py使用Python 3.9风格类型注解 | `safety_gateway/redis_pool.py` | 代码风格 |
| **SG-FIX-R6-05** | sg_config.py和sg_validator.py使用Python 3.9风格类型注解 | `safety_gateway/sg_config.py` + `safety_gateway/sg_validator.py` | 代码风格 |
| **SG-FIX-R6-06** | consent_checker.py使用Python 3.9风格类型注解 | `safety_gateway/consent_checker.py` | 代码风格 |
| **SG-FIX-R6-07** | three_layer_desensitization.py类型注解风格混用 | `app/security/three_layer_desensitization.py` | 代码风格 |
| **SG-FIX-R6-08** | __init__.py为空文件，未导出公共API | `safety_gateway/__init__.py` | 代码增强 |

### 历史遗留项状态

| 修复项 | 描述 | 状态 |
|--------|------|------|
| SG-FIX-R4-06 | _degraded_response死代码 | ✅ 已修复（R6验证确认移除） |
| SG-FIX-R4-07 | desensitization_degraded参数发送但不接收 | ✅ 已修复（R6验证确认参数已使用） |
| SG-FIX-R4-10 | 26/31 SG配置项未在00b文档登记 | ❌ 仍未修复（文档范畴，非代码问题） |

---

## 十、审查结论

### 10.1 总体评估

SafetyGateway轨道在第五轮审查后修复了全部9项遗留问题（100%修复率），本轮审查发现8项新问题，其中3项P1级、5项P2级，**无P0级问题**。

**与前五轮对比**：

| 轮次 | P0级 | P1级 | P2级 | 修复率 |
|------|------|------|------|--------|
| R4 | 2 | 4 | 4 | 70% |
| R5 | 2 | 5 | 2 | 100%(本轮验证) |
| **R6** | **0** | **3** | **5** | — |

问题严重程度持续降低：R4有运行时崩溃风险（P0），R5有代码规范/死代码风险（P0），R6无P0级问题。所有P1级问题均为代码质量改进，无生产运行时风险。

### 10.2 关键风险

1. **SG-FIX-R6-01**（P1）：SGError未映射HTTP状态码，E4005B/E4005C返回HTTP 500。客户端通过重试机制兜底，功能正确但效率低且丢失子错误码信息。
2. **SG-FIX-R6-02**（P1）：两个单例获取函数无线程安全保护，与项目内其他单例模式不一致。
3. **SG-FIX-R6-03**（P1）：类型注解错误，影响静态分析工具和IDE提示。

### 10.3 修复优先级建议

1. **本迭代修复**：SG-FIX-R6-01（HTTP状态码映射）、SG-FIX-R6-02（单例线程安全）、SG-FIX-R6-03（类型注解修正）
2. **下迭代修复**：SG-FIX-R6-04~R6-08（类型注解风格统一、__init__.py导出）

### 10.4 自我反思

| 维度 | 反思 |
|------|------|
| R5修复验证 | R5的9项修复全部落地，修复率100%。第五轮审查质量高，修复建议具体且可操作。 |
| 新问题发现 | 本轮无P0级问题，P1级问题均为代码质量改进而非运行时风险。SG轨道代码质量已达到较高水平。 |
| 审查盲区 | 端到端处理延迟P99<50ms的监控缺失（设计文档要求），但当前仅有HTTP通信开销监控。建议在health.py中增加端到端延迟统计。 |
| 设计偏差合理性 | 代码量超出设计预估17%（3500行 vs 2500-3000行），主要因为降级场景覆盖更全面（10种 vs 设计文档的7种），属于合理偏差。 |
| 规则异构冗余 | 逐条对比确认两套规则在正则模式、替换策略、规则拆分上均存在实质性差异，异构冗余验证通过。Safety规则倾向"关键词前缀+保守匹配"，主规则倾向"直接格式匹配+边界排除"，两者互补。 |
