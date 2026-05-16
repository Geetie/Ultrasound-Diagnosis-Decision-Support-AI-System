# SG安全网关轨道系统性审查报告（第三轮）

**审查日期**：2026-05-10  
**审查范围**：SafetyGateway轨道全部源码、测试、配置及参考文档  
**审查方法**：静态代码分析 + 文档一致性比对 + 测试逻辑审查  
**审查原则**：独立、客观、深入、批判性

---

## 一、审查范围与方法

### 审查范围
- **核心源码**：safety_gateway/ 目录下16个Python文件（约3500行）
- **客户端集成**：safety_gateway_client.py、three_layer_desensitization.py
- **测试文件**：test_safety_gateway.py（193个测试方法）、test_safety_gateway_performance.py（6个测试方法）及6个安全合规测试文件
- **配置文件**：docker-compose.dev.yml、desensitization_rules_safety.yaml、desensitization_rules_safety_patch.yaml
- **参考文档**：01-系统架构设计.md、04-安全合规.md、06-开发环境与进度规划.md、02-核心流程与知识库.md、00b-统一配置参考手册.md

### 审查方法
1. 逐文件静态代码分析，追踪执行路径和异常分支
2. 对照5份核心参考文档进行一致性审查
3. 测试逻辑完整性、Mock正确性、测试隔离性审查
4. 验证前两轮审计20项修复的落地情况

---

## 二、前两轮修复项落地验证结果

| 编号 | 修复项 | 优先级 | 状态 | 备注 |
|------|--------|--------|------|------|
| REMAIN-FIX-01 | ERROR_CODE_MAP添加E4005B/E4005C | P0 | ✅ FIXED | errors.py第181-182行 |
| REMAIN-FIX-02 | is_db_error+WITHDRAWN组合逻辑 | P0 | ⚠️ PARTIAL | 逻辑已重排但WITHDRAWN分支不可达（见SG-FIX-03） |
| REMAIN-FIX-03 | ThresholdCheckResult循环导入 | P0 | ✅ FIXED | 定义移至validation_result.py |
| REMAIN-FIX-04 | ConsentStatus()构造安全化 | P0 | ✅ FIXED | checker.py第268-270行try-except |
| REMAIN-FIX-05~08 | 异常类型测试更新 | P1 | ⚠️ PARTIAL | SGDatabaseUnavailableError已覆盖，SGCombinedUnavailableError无测试 |
| REMAIN-FIX-09 | /health语义化HTTP状态码 | P1 | ❌ NOT FIXED | result["overall"]键名错误，应为result["status"]（见SG-FIX-01） |
| REMAIN-FIX-10 | compact模式Redis降级 | P1 | ✅ FIXED | health.py第56-60行 |
| REMAIN-FIX-11 | 白名单字段名对齐 | P1 | ❌ NOT FIXED | anatomical_position等6个字段名与设计文档不一致（见SG-FIX-18） |
| REMAIN-FIX-12 | FORBIDDEN_GRADING_TERMS递归过滤 | P1 | ✅ FIXED | _filter_forbidden_terms已实现递归 |
| REMAIN-FIX-13 | 7种降级场景端到端测试 | P1 | ✅ FIXED | TestDegradedPathSevenScenarios含7个方法 |
| REMAIN-FIX-14 | 规则热更新测试 | P1 | ❌ NOT FIXED | _rules_sync_subscriber无任何测试（见SG-FIX-17） |
| REMAIN-FIX-15 | 无同意记录返回PENDING | P1 | ✅ FIXED | consent_checker.py第89行 |
| REMAIN-FIX-16 | 延迟采样并发安全 | P2 | ✅ FIXED | deque+Lock已实现 |
| REMAIN-FIX-17 | 熔断器测试teardown清理 | P2 | ❌ NOT FIXED | 内联清理非teardown，测试失败时泄漏（见SG-FIX-21） |
| REMAIN-FIX-18 | ThresholdCheckResult导入路径 | P2 | ✅ FIXED | 统一从validation_result导入 |
| REMAIN-FIX-19 | 清理allowed_origins配置 | P2 | ✅ FIXED | sg_config.py中已移除 |
| REMAIN-FIX-20 | _start_time改为实例变量 | P2 | ❌ NOT FIXED | 变量已改但classmethod仍用cls访问（见SG-FIX-02） |

**汇总**：20项中12项FIXED，4项NOT FIXED，2项PARTIALLY FIXED，2项PARTIALLY FIXED（计入NOT FIXED）

---

## 三、新发现的问题（按严重程度分级）

### SG-FIX-01: /health端点result["overall"]键名Bug导致必然500错误
- **严重程度**: P0
- **类别**: 运行时错误
- **文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L174) 第174行
- **问题描述**: `/health`端点使用`result["overall"]`判断健康状态，但`health.py`的`check_all()`返回的字典键名为`"status"`而非`"overall"`。调用`result["overall"]`必然抛出`KeyError`，导致`/health`端点始终返回500内部错误。
- **设计文档要求**: 01-系统架构设计 §3.4 要求健康检查端点正常工作；06-开发环境 §4 要求http://localhost:9001/health正常工作
- **影响**: 健康检查完全失效，Docker Compose healthcheck始终失败，SG容器可能被标记为unhealthy并重启，主服务无法启动（depends_on condition: service_healthy）
- **修复方案**: 将`result["overall"]`改为`result["status"]`，涉及main.py第174、176、178行
- **验证方法**: 调用`GET /health`，验证healthy返回200、degraded返回200+X-SG-Status头、unhealthy返回503

### SG-FIX-02: get_start_time/get_uptime为classmethod访问实例变量导致AttributeError
- **严重程度**: P0
- **类别**: 运行时错误
- **文件**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L261-L267) 第261-267行
- **问题描述**: `_start_time`在`__init__`中通过`self._start_time`赋值为实例变量，但`get_start_time()`和`get_uptime()`仍为`@classmethod`，使用`cls._start_time`访问。classmethod无法访问实例属性，调用时必然抛出`AttributeError: type object 'HealthChecker' has no attribute '_start_time'`。
- **设计文档要求**: 05-可观测性要求服务启动时间和运行时间可查询
- **影响**: 任何调用`HealthChecker.get_start_time()`或`HealthChecker.get_uptime()`的代码都会崩溃，/health/watchdog端点可能受影响
- **修复方案**: 将两个方法改为实例方法（移除`@classmethod`，`cls`改为`self`），或将`_start_time`改为类变量
- **验证方法**: 调用`HealthChecker().get_start_time()`和`HealthChecker().get_uptime()`无异常

### SG-FIX-03: is_db_error=True时WITHDRAWN分支不可达（死代码）
- **严重程度**: P0
- **类别**: 安全漏洞/逻辑缺陷
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L280-L296) 第280-296行
- **问题描述**: 当`is_db_error=True`时，追踪consent_checker.py所有返回`error=True`的路径，`consent_status`恒为`None`。因此第281行`if consent_status == ConsentStatus.WITHDRAWN:`永远为False，第282-296行整个分支是不可达代码。`DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN`枚举值在perform_full_check中永远不会被设置。
- **设计文档要求**: 01-系统架构设计 §3.4 要求E4005A错误码可达；04-安全合规 §2 要求"SG不可用+患者已撤回同意"场景有明确处理
- **影响**: E4005A错误码在DB错误路径下不可达，"SG不可用+患者撤回同意"这一关键安全场景缺少正确的处理路径
- **修复方案**: 在consent_checker.py中，当DB错误但Redis缓存命中WITHDRAWN状态时，应设置`is_db_error=True`且`consent_status=WITHDRAWN`，使该分支可达
- **验证方法**: 构造测试：Redis缓存返回WITHDRAWN + DB查询失败 → 验证返回E4005A

### SG-FIX-04: is_timeout优先于WITHDRAWN/DENIED同意状态检查（安全漏洞）
- **严重程度**: P0
- **类别**: 安全漏洞
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L312-L355) 第312行 vs 第331行
- **问题描述**: 当DB查询超时但Redis缓存命中时，`is_timeout=True`且`consent_status`可能是WITHDRAWN或DENIED。代码在第312行先检查`is_timeout`返回DEGRADED（允许降级输出），而非在第331行检查WITHDRAWN/DENIED返回FAILED。这违反了"患者撤回同意必须拒绝所有输出"的核心安全规则。
- **设计文档要求**: 04-安全合规 §2 "患者撤回知情同意后，系统必须立即停止所有诊断输出"；01-系统架构设计 §1 A级安全标准"零容忍策略"
- **影响**: 已撤回同意的患者在DB超时+Redis缓存命中WITHDRAWN的场景下，可能获得降级输出而非被拒绝，构成医疗安全合规风险
- **修复方案**: 将同意状态检查（WITHDRAWN/DENIED/PENDING）移到`is_timeout`检查之前，确保无论是否超时，已撤回/拒绝的同意始终优先拒绝
- **验证方法**: 构造测试：Redis缓存返回WITHDRAWN + DB超时 → 验证返回FAILED而非DEGRADED

### SG-FIX-05: _filter_forbidden_terms只替换第一个匹配的禁止术语
- **严重程度**: P0
- **类别**: 逻辑错误
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L575-L584) 第575-584行
- **问题描述**: `_filter_forbidden_terms`在字符串中找到第一个禁止术语后立即`return`，导致同一字符串中的其他禁止术语未被过滤。例如`"TI-RADS 4类，恶性可能"`只会替换第一个匹配项。此外，`FORBIDDEN_GRADING_TERMS`是set类型，迭代顺序不确定，导致相同输入可能产生不同输出。
- **设计文档要求**: 01-系统架构设计 §3.4 降级输出必须过滤所有分级术语
- **影响**: 降级输出中可能残留分级术语（如BI-RADS、恶性），违反"先校验后输出"的安全原则
- **修复方案**: 将`return value.replace(term, "[REDACTED_GRADED]")`改为`value = value.replace(term, "[REDACTED_GRADED]")`，循环结束后再return
- **验证方法**: 测试`_filter_forbidden_terms("TI-RADS 4类恶性结节")`，验证所有禁止术语均被替换

### SG-FIX-06: 熔断器OPEN后绕过熔断器直接查PG，违背熔断器设计
- **严重程度**: P0
- **类别**: 安全漏洞/设计偏差
- **文件**: [consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L173-L176) 第173-176行
- **问题描述**: 熔断器OPEN后，代码在Redis缓存未命中时直接查询PG。但熔断器打开意味着PG连续失败5次，此时直接查询PG违背熔断器保护目的，高并发下可能引发雪崩效应。且此路径无超时保护（虽然_query_consent_status内部有asyncio.timeout，但熔断器打开后不应再尝试PG）。
- **设计文档要求**: 01-系统架构设计 §3.4 熔断器模式用于保护后端服务；模块文档第8行"Redis缓存未命中时，拦截所有输出（E4005B）"
- **影响**: 熔断器形同虚设，PG故障期间大量请求直接打到故障数据库，加剧故障
- **修复方案**: 熔断器OPEN+Redis缓存未命中时，直接返回`ConsentCheckResult(None, False, True)`（拦截输出E4005B），而非尝试直接查PG
- **验证方法**: 构造测试：熔断器OPEN + Redis缓存未命中 → 验证返回error=True而非查询PG

### SG-FIX-07: Redis缓存TTL 60秒对同意状态过长，存在安全风险
- **严重程度**: P0
- **类别**: 安全漏洞
- **文件**: [consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L34) 第34行
- **问题描述**: `REDIS_CONSENT_TTL_SECONDS = 60`，患者撤回同意后Redis缓存中可能仍保留"granted"状态长达60秒。在此窗口期内，走Redis缓存fallback路径的请求会读到过期的"granted"状态，允许对已撤回同意的患者输出诊断结果。
- **设计文档要求**: 04-安全合规 §2 "患者撤回知情同意后，系统必须立即停止所有诊断输出"
- **影响**: 患者撤回同意后最长60秒内仍可能获得诊断输出，构成医疗安全合规风险
- **修复方案**: (1) 将TTL缩短到5-10秒；(2) 患者撤回同意时主动删除对应Redis缓存键；(3) 在Redis缓存fallback路径中对"granted"状态增加短TTL二次确认
- **验证方法**: 构造测试：患者撤回同意 → 验证Redis缓存立即失效或5秒内失效

### SG-FIX-08: threshold _lazy_load并发缺陷——不等待首次加载完成
- **严重程度**: P0
- **类别**: 运行时错误/并发缺陷
- **文件**: [threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py#L76-L77) 第76-77行
- **问题描述**: `load_thresholds_from_db()`中，如果`_load_in_progress=True`则直接返回，不等待加载完成。并发场景下，请求B调用`ensure_loaded()`发现`_loaded=False`，调用`_lazy_load()`进入`load_thresholds_from_db()`，发现`_load_in_progress=True`后立即返回，继续使用未加载的阈值（默认值0.90）。
- **设计文档要求**: 01-系统架构设计 §3.4 阈值引擎必须正确加载后才能执行校验
- **影响**: 首次加载期间的并发请求使用默认阈值0.90而非数据库中的实际阈值，可能导致本应被拦截的高置信度诊断通过校验
- **修复方案**: 使用`asyncio.Event`替代`_load_in_progress`标志，让后续调用者等待首次加载完成
- **验证方法**: 构造并发测试：多个协程同时调用ensure_loaded()，验证全部等待加载完成

### SG-FIX-09: Redis URL替换逻辑对db编号>=10场景产生错误结果
- **严重程度**: P1
- **类别**: 逻辑错误
- **文件**: [redis_pool.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/redis_pool.py#L18-L19) 第18-19行
- **问题描述**: `sg_redis_url.replace("/0", "/1")`使用全局字符串替换，对db编号>=10的场景产生错误结果。例如`redis://localhost:6379/10`会变成`redis://localhost:6379/11`（db 10→11），而非预期的db 1。
- **设计文档要求**: 00b-统一配置参考手册 §6 Redis配置
- **影响**: 如果Redis配置使用db编号>=10，SG会连接到错误的Redis数据库
- **修复方案**: 使用URL解析（如`urllib.parse.urlparse`）仅替换路径末尾的db编号，或使用正则`re.sub(r'/\d+$', '/1', url)`
- **验证方法**: 测试URL `redis://localhost:6379/10`替换后为`redis://localhost:6379/1`

### SG-FIX-10: _persist_threshold_to_db与get_db_session双重commit
- **严重程度**: P1
- **类别**: 逻辑错误
- **文件**: [threshold.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/threshold.py#L337) 第337行、[sg_db.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_db.py#L32) 第32行
- **问题描述**: `_persist_threshold_to_db`在`get_db_session()`上下文内显式调用`session.commit()`，而`get_db_session()`退出时也会自动`await session.commit()`，导致每次持久化操作执行两次commit。
- **设计文档要求**: 数据库操作应正确管理事务边界
- **影响**: 冗余commit通常无害，但在极端情况下（第一次commit后连接断开）可能产生误导性错误日志
- **修复方案**: 移除`_persist_threshold_to_db`和`_delete_threshold_to_db`中的显式`session.commit()`，依赖`get_db_session()`的自动commit
- **验证方法**: 运行阈值更新测试，验证只执行一次commit

### SG-FIX-11: _rules_sync_subscriber pubsub资源泄漏
- **严重程度**: P1
- **类别**: 资源泄漏
- **文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L122) 第122行
- **问题描述**: `_rules_sync_subscriber`创建pubsub时未使用`async with`上下文管理器，任务取消时pubsub连接不会被正确清理。对比threshold.py的`_subscribe_loop`正确使用了`async with redis.pubsub() as pubsub`。
- **设计文档要求**: 05-可观测性要求资源正确管理
- **影响**: 服务关闭时Redis连接泄漏，长期运行可能耗尽连接池
- **修复方案**: 将`pubsub = redis_client.pubsub()`改为`async with redis_client.pubsub() as pubsub:`
- **验证方法**: 启动SG服务→关闭→验证无Redis连接泄漏

### SG-FIX-12: _rules_sync_subscriber异常后不重连，热更新永久失效
- **严重程度**: P1
- **类别**: 功能缺陷
- **文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L135-L136) 第135-136行
- **问题描述**: `_rules_sync_subscriber`在非CancelledError异常后直接退出，不会重连。与threshold.py的`_subscribe_loop`（有while循环和重试）不同。如果Redis短暂断开，脱敏规则热更新功能将永久失效，直到服务重启。
- **设计文档要求**: 01-系统架构设计 §3.4 规则热更新应持续可用
- **影响**: Redis短暂故障后脱敏规则无法热更新，需重启服务才能恢复
- **修复方案**: 添加while循环和指数退避重试，与threshold.py的_subscribe_loop保持一致
- **验证方法**: 模拟Redis断开→恢复→验证规则热更新恢复

### SG-FIX-13: reload_rules()无锁保护，可能读到中间状态
- **严重程度**: P1
- **类别**: 并发安全
- **文件**: [desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L315-L391) 第315-391行
- **问题描述**: `reload_rules()`保存旧状态和赋值新状态之间无锁保护。如果`reload_rules()`在`asyncio.to_thread`中被调用（如main.py第130行），而同时另一个线程正在读取`self._rules`或`self._compiled_patterns`，可能出现不一致的中间状态。
- **设计文档要求**: 规则热更新应保证原子性
- **影响**: 热更新期间可能读到部分更新的规则集，导致脱敏检查不一致
- **修复方案**: 添加`threading.Lock`保护`_rules`和`_compiled_patterns`的读写
- **验证方法**: 并发测试：同时调用reload_rules()和check_sensitive_data()

### SG-FIX-14: 默认规则与YAML规则手机号脱敏replacement不同步
- **严重程度**: P1
- **类别**: 数据不一致
- **文件**: [desensitization.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/desensitization.py#L127) 第127行 vs [desensitization_rules_safety.yaml](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/desensitization_rules_safety.yaml#L28) 第28行
- **问题描述**: 默认规则`sg_phone`的replacement为`r"\1****\3"`（保留前3后4），而YAML规则为`'*******\3'`（7星+后4，前3丢失）。YAML文件第14行注释"此文件必须与desensitization.py _get_default_rules()保持同步"，但实际未同步。
- **设计文档要求**: 04-安全合规 §2 规则异构冗余应保证基本一致性
- **影响**: YAML加载失败回退到默认规则时，手机号脱敏行为变化（从丢失前3变为保留前3），降低脱敏强度
- **修复方案**: 统一两处replacement为`r"\1****\3"`（保留前3后4），或统一为`'*******\3'`（更严格的脱敏）
- **验证方法**: 对比两处replacement值一致

### SG-FIX-15: 熔断器OPEN后PG查询成功但未写入Redis缓存
- **严重程度**: P1
- **类别**: 功能缺陷
- **文件**: [consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L173-L176) 第173-176行
- **问题描述**: 正常路径PG查询成功后调用`_cache_consent_to_redis`写入缓存，但CircuitBreakerError fallback路径中PG直接查询成功后未调用缓存写入。后续相同患者请求无法命中Redis缓存，持续打到PG。
- **影响**: 熔断器打开期间PG本已脆弱，缺少缓存写入持续增加PG负载
- **修复方案**: 在第175行后添加`await self._cache_consent_to_redis(patient_id, consent_status)`
- **验证方法**: 构造测试：熔断器OPEN+Redis缓存未命中+PG查询成功 → 验证Redis缓存已写入

### SG-FIX-16: _retry_query总时间预算检查未包含下一次查询耗时
- **严重程度**: P1
- **类别**: 逻辑错误
- **文件**: [consent_checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/consent_checker.py#L102) 第102行、第130行
- **问题描述**: 重试预算检查`if elapsed + delay <= self._retry_max_total_time`只判断了"已耗时+下一次休眠延迟"是否超预算，未考虑下一次查询本身的耗时（最多5秒）。实际总时间可超出max_total_time预算。
- **影响**: 重试总时间可能超出10秒预算，增加请求延迟
- **修复方案**: 改为`if elapsed + delay + self._query_timeout <= self._retry_max_total_time`
- **验证方法**: 构造测试：DB查询超时5秒 → 验证总重试时间不超过10秒

### SG-FIX-17: _rules_sync_subscriber无任何测试覆盖
- **严重程度**: P1
- **类别**: 测试质量
- **文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L119-L136) 第119-136行
- **问题描述**: `_rules_sync_subscriber`是脱敏规则热更新的核心机制，通过Redis Pub/Sub监听`desensitization_rules_update`频道并调用`reload_rules()`。完全无测试覆盖，包括：收到消息后触发reload的路径、reload失败时的错误处理、订阅异常时的错误处理、CancelledError的优雅退出。
- **设计文档要求**: 06-开发环境 §5 测试用例应覆盖核心功能路径
- **影响**: 规则热更新功能无测试保障，任何代码变更可能破坏此功能而不被察觉
- **修复方案**: 添加测试类`TestRulesSyncSubscriber`，覆盖上述4个场景
- **验证方法**: 运行新增测试全部通过

### SG-FIX-18: 白名单字段名与设计文档不一致
- **严重程度**: P1
- **类别**: 文档偏差
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L32-L42) 第32-42行、[sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py#L15-L20) 第15-20行
- **问题描述**: 代码中6个白名单字段名与设计文档02-核心流程与知识库.md不一致：`anatomical_position`→`anatomical_location`、`echo_characteristics`→`echogenicity`、`boundary_characteristics`→`boundary`、`morphological_features`→`morphology`、`blood_flow_characteristics`→`vascularity`、`calcification_characteristics`→`calcification`。
- **设计文档要求**: 02-核心流程与知识库.md第269行定义了标准字段名
- **影响**: 如果融合层使用设计文档中的字段名，SG白名单过滤将无法匹配这些字段，导致降级输出中遗漏本应保留的观察描述字段
- **修复方案**: 将代码中字段名改为与设计文档一致，或在白名单中同时包含两套字段名
- **验证方法**: 对比代码字段名与设计文档字段名完全一致

### SG-FIX-19: SGCombinedUnavailableError (E4005C) 无测试覆盖
- **严重程度**: P1
- **类别**: 测试质量
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L302-L306) 第302-306行
- **问题描述**: 当`is_db_error=True`且`fusion_available=False`时，代码抛出`SGCombinedUnavailableError`（E4005C），但整个测试文件中无任何测试覆盖此异常路径。E4005C是架构文档中明确要求的"SafetyGateway+融合层同时不可用"场景。
- **设计文档要求**: 01-系统架构设计 §3.4 E4005C错误码必须可达且有测试
- **影响**: E4005C路径无测试保障，代码变更可能破坏此关键安全路径
- **修复方案**: 添加测试：mock `is_db_error=True` + `fusion_available=False` → 验证抛出SGCombinedUnavailableError
- **验证方法**: 运行新增测试通过

### SG-FIX-20: consent_status_at_input快速拒绝路径遗漏"pending"状态
- **严重程度**: P1
- **类别**: 功能缺陷
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L255) 第255行
- **问题描述**: 快速拒绝路径`if consent_status_at_input in ("withdrawn", "denied"):`遗漏了"pending"状态。如果调用方在输入时已知同意状态为pending，仍会进入完整的DB查询流程，浪费资源且延迟了本可快速拒绝的请求。
- **设计文档要求**: 04-安全合规 §2 入口快速拒绝应覆盖所有非granted状态
- **影响**: pending状态的请求无法被快速拒绝，增加不必要的DB查询和延迟
- **修复方案**: 改为`if consent_status_at_input in ("withdrawn", "denied", "pending"):`
- **验证方法**: 测试`consent_status_at_input="pending"`时快速拒绝

### SG-FIX-21: 熔断器测试teardown清理不完整
- **严重程度**: P2
- **类别**: 测试质量
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py#L1016-L1033) 第1016-1033行
- **问题描述**: `TestDegradedPathScenarios.test_checker_with_circuit_breaker_open`在第1033行以内联方式清理`circuit_breaker_manager._breakers`，而非在`teardown_method`中。如果测试在到达第1033行之前失败，"sg_db_consent" breaker将泄漏到全局manager中。
- **影响**: 测试失败时全局状态污染，影响后续测试
- **修复方案**: 添加`teardown_method`清理`circuit_breaker_manager._breakers`
- **验证方法**: 故意让测试断言失败，验证teardown正确清理

### SG-FIX-22: 6个异步方法mock缺少new_callable=AsyncMock
- **严重程度**: P2
- **类别**: 测试质量
- **文件**: [test_safety_gateway.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/tests/unit/test_safety_gateway.py) 第1512、1527、1556、1567、1586、2037、2120行
- **问题描述**: 7处对`check_consent_status`（async方法）使用`patch.object`时未指定`new_callable=AsyncMock`，创建的是`MagicMock`而非`AsyncMock`。对普通元组执行`await`会抛出`TypeError: object tuple can't be used in 'await' expression`。
- **影响**: 这些测试在运行时很可能直接失败
- **修复方案**: 添加`new_callable=AsyncMock`参数
- **验证方法**: 运行受影响的测试全部通过

### SG-FIX-23: get_safety_gateway_checker单例模式非线程安全
- **严重程度**: P2
- **类别**: 并发安全
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L691-L696) 第691-696行
- **问题描述**: `get_safety_gateway_checker()`使用全局变量但无锁保护，多线程环境下可能创建多个实例。对比consent_checker.py第213-218行的正确实现（双重检查锁定+Lock）。
- **影响**: 多线程环境下可能创建多个checker实例，违反单例语义
- **修复方案**: 添加双重检查锁定模式，与consent_checker.py保持一致
- **验证方法**: 多线程并发调用get_safety_gateway_checker()，验证只创建一个实例

### SG-FIX-24: CORS配置硬编码，不支持HTTPS
- **严重程度**: P2
- **类别**: 设计偏差
- **文件**: [main.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/main.py#L146-L157) 第146-157行
- **问题描述**: CORS `allowed_origins`硬编码为`http://localhost:8000`和`http://main-service:8000`，无法通过环境变量配置，不支持HTTPS，生产环境部署时必须修改代码。
- **修复方案**: 将`allowed_origins`移至sg_config.py作为可配置项，支持HTTPS来源
- **验证方法**: 通过环境变量配置allowed_origins，验证CORS行为正确

### SG-FIX-25: _get_memory_usage_mb在Windows上psutil未安装时静默返回0.0
- **严重程度**: P2
- **类别**: 功能缺陷
- **文件**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L99-L112) 第99-112行
- **问题描述**: fallback路径读取`/proc/{pid}/status`是Linux专属路径，Windows上psutil未安装时静默返回0.0，内存监控完全失效且无任何告警。
- **修复方案**: 添加Windows兼容fallback或至少记录warning日志，返回值改为None或-1以区分"无法获取"
- **验证方法**: 在Windows上无psutil环境验证health check行为

### SG-FIX-26: compact模式降级逻辑掩盖非Redis故障的严重性
- **严重程度**: P2
- **类别**: 设计偏差
- **文件**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L56-L60) 第56-60行
- **问题描述**: compact模式下，当Redis unhealthy且其他组件（如数据库）也unhealthy时，`overall`从unhealthy降级为degraded，掩盖了数据库故障的严重性。
- **修复方案**: 降级逻辑仅在Redis是唯一unhealthy组件时生效
- **验证方法**: 构造测试：Redis+DB同时unhealthy → 验证overall仍为unhealthy

### SG-FIX-27: 21个SG配置项未在00b统一配置参考手册中登记
- **严重程度**: P2
- **类别**: 文档偏差
- **文件**: [sg_config.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/sg_config.py)
- **问题描述**: sg_config.py中21个SG专有配置项未在00b-统一配置参考手册中登记，违反"唯一权威来源"原则。
- **设计文档要求**: 00b-统一配置参考手册声明"本文档是系统所有配置项的唯一权威来源"
- **修复方案**: 将21个配置项补充登记到00b文档
- **验证方法**: 逐一核对sg_config.py中的配置项在00b文档中均有记录

### SG-FIX-28: SG_DEPLOYMENT_MODE与DEPLOYMENT_PROFILE命名不一致
- **严重程度**: P2
- **类别**: 文档偏差
- **文件**: [health.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/health.py#L57) 第57行
- **问题描述**: health.py使用`os.environ.get("SG_DEPLOYMENT_MODE")`读取部署模式，但所有设计文档统一使用`DEPLOYMENT_PROFILE`作为配置项名称。
- **修复方案**: 将`SG_DEPLOYMENT_MODE`改为`DEPLOYMENT_PROFILE`
- **验证方法**: 设置`DEPLOYMENT_PROFILE=compact`验证compact模式生效

### SG-FIX-29: _filter_forbidden_terms无递归深度限制
- **严重程度**: P2
- **类别**: 代码质量
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L573-L589) 第573-589行
- **问题描述**: 与`check_desensitization`方法有`MAX_RECURSION_DEPTH=10`保护不同，`_filter_forbidden_terms`的递归无深度限制，深度嵌套结构可能导致栈溢出。
- **修复方案**: 添加递归深度参数，超过MAX_RECURSION_DEPTH时停止递归
- **验证方法**: 构造深度嵌套dict测试，验证不触发RecursionError

### SG-FIX-30: desensitization_degraded参数声明但从未使用
- **严重程度**: P2
- **类别**: 代码质量
- **文件**: [checker.py](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/safety_gateway/checker.py#L247) 第247行
- **问题描述**: `desensitization_degraded: bool = False`在方法签名中声明但在方法体中从未引用。应实现脱敏引擎降级时的更严格处理逻辑，或移除该参数。
- **修复方案**: 实现该参数的逻辑（脱敏降级时更严格处理）或移除该参数
- **验证方法**: 代码审查确认参数已使用或已移除

---

## 四、与参考文档的偏差分析

| 编号 | 严重程度 | 来源文档 | 偏差描述 |
|------|----------|----------|----------|
| SG-DEV-01 | 中 | 04-安全合规 | desensitization_rules_safety.yaml注释要求手动同步，但设计文档要求从主规则源自动生成 |
| SG-DEV-02 | 低 | 06-开发环境 | 代码量约3500行，超出设计预估2500-3000行 |
| SG-DEV-03 | 低 | 06-开发环境 | 测试用例约199个，超出设计预估80-120个（正向偏差，覆盖更充分） |
| SG-DEV-04 | 低 | 06-开发环境 | 健康检查间隔4s与06文档示例5s不一致（与01文档一致） |
| SG-DEV-05 | 高 | 00b-配置手册 | 21个SG专有配置项未在00b文档中登记 |
| SG-DEV-06 | 中 | 00b-配置手册 | db_pool_size默认值5与00b文档PG_POOL_MAX_SIZE不一致 |
| SG-DEV-07 | 中 | 00b-配置手册 | SAFETY_GATEWAY_HEALTH_CHECK_INTERVAL/TIMEOUT在00b中被引用但未实现 |
| SG-DEV-08 | 低 | 00b-配置手册 | CONSENT_CACHE_TTL_SECONDS硬编码为60而非从配置读取 |
| SG-DEV-09 | 中 | 跨文档 | health.py使用SG_DEPLOYMENT_MODE而非统一的DEPLOYMENT_PROFILE |
| SG-DEV-10 | 低 | 跨文档 | 01文档健康检查间隔4s与06文档示例5s矛盾 |
| SG-DEV-11 | 低 | 跨文档 | 降级白名单为9个具体字段而非设计文档描述的单一observations字段（功能等价） |

---

## 五、SafetyGateway轨道修复项声明

### 显式声明：以下修复项属于SafetyGateway轨道

| 编号 | 严重程度 | 类别 | 修复项标题 | 涉及文件 |
|------|----------|------|-----------|----------|
| SG-FIX-01 | **P0** | 运行时错误 | /health端点result["overall"]键名Bug | main.py |
| SG-FIX-02 | **P0** | 运行时错误 | get_start_time/get_uptime为classmethod访问实例变量 | health.py |
| SG-FIX-03 | **P0** | 安全漏洞 | is_db_error=True时WITHDRAWN分支不可达 | checker.py, consent_checker.py |
| SG-FIX-04 | **P0** | 安全漏洞 | is_timeout优先于WITHDRAWN/DENIED同意状态检查 | checker.py |
| SG-FIX-05 | **P0** | 逻辑错误 | _filter_forbidden_terms只替换第一个匹配项 | checker.py |
| SG-FIX-06 | **P0** | 安全漏洞 | 熔断器OPEN后绕过熔断器直接查PG | consent_checker.py |
| SG-FIX-07 | **P0** | 安全漏洞 | Redis缓存TTL 60秒对同意状态过长 | consent_checker.py |
| SG-FIX-08 | **P0** | 并发缺陷 | threshold _lazy_load不等待首次加载完成 | threshold.py |
| SG-FIX-09 | P1 | 逻辑错误 | Redis URL替换逻辑对db>=10错误 | redis_pool.py |
| SG-FIX-10 | P1 | 逻辑错误 | _persist_threshold_to_db双重commit | threshold.py, sg_db.py |
| SG-FIX-11 | P1 | 资源泄漏 | _rules_sync_subscriber pubsub资源泄漏 | main.py |
| SG-FIX-12 | P1 | 功能缺陷 | _rules_sync_subscriber异常后不重连 | main.py |
| SG-FIX-13 | P1 | 并发安全 | reload_rules()无锁保护 | desensitization.py |
| SG-FIX-14 | P1 | 数据不一致 | 默认规则与YAML规则手机号脱敏不同步 | desensitization.py, YAML |
| SG-FIX-15 | P1 | 功能缺陷 | 熔断器OPEN后PG查询成功未写入Redis缓存 | consent_checker.py |
| SG-FIX-16 | P1 | 逻辑错误 | _retry_query总时间预算检查未包含查询耗时 | consent_checker.py |
| SG-FIX-17 | P1 | 测试质量 | _rules_sync_subscriber无任何测试覆盖 | test_safety_gateway.py |
| SG-FIX-18 | P1 | 文档偏差 | 白名单字段名与设计文档不一致 | checker.py, sg_config.py |
| SG-FIX-19 | P1 | 测试质量 | SGCombinedUnavailableError (E4005C) 无测试覆盖 | test_safety_gateway.py |
| SG-FIX-20 | P1 | 功能缺陷 | 快速拒绝路径遗漏pending状态 | checker.py |
| SG-FIX-21 | P2 | 测试质量 | 熔断器测试teardown清理不完整 | test_safety_gateway.py |
| SG-FIX-22 | P2 | 测试质量 | 6个异步方法mock缺少AsyncMock | test_safety_gateway.py |
| SG-FIX-23 | P2 | 并发安全 | get_safety_gateway_checker单例非线程安全 | checker.py |
| SG-FIX-24 | P2 | 设计偏差 | CORS配置硬编码不支持HTTPS | main.py |
| SG-FIX-25 | P2 | 功能缺陷 | Windows上内存监控静默返回0.0 | health.py |
| SG-FIX-26 | P2 | 设计偏差 | compact模式降级掩盖非Redis故障 | health.py |
| SG-FIX-27 | P2 | 文档偏差 | 21个SG配置项未在00b文档登记 | sg_config.py, 00b文档 |
| SG-FIX-28 | P2 | 文档偏差 | SG_DEPLOYMENT_MODE与DEPLOYMENT_PROFILE不一致 | health.py |
| SG-FIX-29 | P2 | 代码质量 | _filter_forbidden_terms无递归深度限制 | checker.py |
| SG-FIX-30 | P2 | 代码质量 | desensitization_degraded参数声明但未使用 | checker.py |

### 修复优先级排序

**P0（8项，必须立即修复）**：
1. SG-FIX-01: /health键名Bug → 导致健康检查完全失效
2. SG-FIX-02: classmethod访问实例变量 → 运行时必然崩溃
3. SG-FIX-03: WITHDRAWN分支不可达 → E4005A错误码不可达
4. SG-FIX-04: is_timeout优先于WITHDRAWN → 安全合规风险
5. SG-FIX-05: 禁止术语过滤只替换第一个 → 降级输出残留分级术语
6. SG-FIX-06: 熔断器OPEN后直接查PG → 雪崩风险
7. SG-FIX-07: Redis缓存TTL 60秒 → 撤回同意后仍可输出
8. SG-FIX-08: _lazy_load并发缺陷 → 使用未加载阈值

**P1（12项，应尽快修复）**：
9. SG-FIX-09 ~ SG-FIX-20

**P2（10项，可安排修复）**：
21. SG-FIX-21 ~ SG-FIX-30

---

## 六、改进建议

### 6.1 架构层面
1. **引入配置中心**：将SG_DEPLOYMENT_MODE、CORS allowed_origins、CONSENT_CACHE_TTL等硬编码值统一纳入sg_config.py管理
2. **熔断器fallback路径重新设计**：熔断器OPEN后不应直接查PG，应返回error=True让上层决定
3. **规则热更新统一框架**：desensitization和threshold的Redis Pub/Sub订阅应使用统一的重连和错误处理模式

### 6.2 测试层面
1. **添加SGCombinedUnavailableError测试**：E4005C是Gate 1必须通过项
2. **添加_rules_sync_subscriber测试**：规则热更新是核心功能
3. **修复异步mock**：7处缺少AsyncMock的测试需修复
4. **添加teardown清理**：4个全局状态污染的测试类需添加teardown_method
5. **添加_filter_forbidden_terms直接测试**：当前仅间接覆盖

### 6.3 安全合规层面
1. **缩短Redis缓存TTL**：从60秒缩短到5-10秒，或实现主动失效
2. **同意状态检查优先级**：WITHDRAWN/DENIED检查必须先于is_timeout检查
3. **白名单字段名对齐**：与设计文档保持一致，避免降级输出遗漏字段

---

## 七、审查结论

SG安全网关轨道的核心架构设计合理，进程隔离、四项核心校验、7种降级场景、OOM保护等关键功能均已实现。但本次审查发现**8个P0级问题**，其中4个涉及医疗安全合规风险（SG-FIX-03/04/06/07），2个涉及运行时崩溃（SG-FIX-01/02），2个涉及逻辑错误（SG-FIX-05/08）。

**最紧急的修复项**：
1. **SG-FIX-01**（/health键名Bug）— 修复量最小但影响最大，1行代码修改即可恢复健康检查
2. **SG-FIX-04**（is_timeout优先于WITHDRAWN）— 医疗安全合规风险，需调整检查顺序
3. **SG-FIX-07**（Redis缓存TTL过长）— 医疗安全合规风险，需缩短TTL或实现主动失效

建议按P0→P1→P2的顺序修复，P0项应在下一个工作日内完成。
