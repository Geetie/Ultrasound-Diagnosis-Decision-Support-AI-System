# SafetyGateway (SG安全网关) 轨道系统性审查报告

**审查日期**：2026-05-11  
**审查范围**：`safety_gateway/` 全部17个Python文件、4个YAML规则文件、4个Docker/部署配置文件、客户端集成代码、227个单元测试+6个性能测试  
**审查方法**：静态代码分析 + 文档一致性比对 + 测试覆盖率分析 + 配置合规性验证  
**参考文档**：01-系统架构设计.md (P0)、04-安全合规.md (P0)、06-开发环境与进度规划.md (P1)、02-核心流程与知识库.md (P2)、00b-统一配置参考手册.md (P2)

---

## 1. 审查概述

### 1.1 代码规模统计

| 指标 | 实际值 | 设计文档预估值 | 偏差 |
|------|--------|---------------|------|
| Python文件数 | 17 | - | - |
| 总行数 | 3925 | 2500-3000 | **超出31%** |
| 有效代码行 | ~3473 | 2500-3000 | **超出16%** |
| 测试类数 | 56 | - | - |
| 测试方法数 | 227 | 80-120 | **超出89%** |
| 性能测试方法数 | 6 | - | - |

### 1.2 总体评价

SafetyGateway轨道的实现整体质量**良好**，核心架构设计（独立进程、四阶段校验、7种降级场景、OOM保护）与设计文档高度一致。代码依赖隔离严格，无任何对主服务或融合层的代码级依赖。测试覆盖远超预期。

但存在**2个P0级问题**、**8个P1级问题**和**7个P2级问题**需要修复，其中最关键的是SG未自主实现融合层健康检查（依赖调用方传入参数）和配置默认值与设计文档不一致。

---

## 2. 代码质量评估结果

### 2.1 健壮性

| 检查项 | 结果 | 说明 |
|--------|------|------|
| A1-01 独立uvicorn实例 | ✅ 通过 | `main.py`的`__main__`块、`Dockerfile.safety-gateway.dev`、`supervisor/safety-gateway.conf`均配置独立uvicorn进程 |
| A1-02 主服务崩溃时独立存活 | ✅ 通过 | Docker Compose独立容器、SG无app模块依赖、`TestMainServiceCrashScenario`覆盖E4005/E4005B/E4005C |
| A1-03 OOM保护 | ✅ 通过 | `docker-compose.dev.yml`设置`oom_score_adj=-1000`、`start_safety_gateway.sh`写入`/proc/self/oom_score_adj`、`health.py`的`_check_oom_protection()`运行时检查 |
| A1-04 健康检查端点 | ✅ 通过 | `/health`端点、Docker Compose配置`interval=4s, timeout=1s, retries=3, start_period=5s` |
| A1-05 内存预算 | ✅ 通过 | `sg_memory_budget_mb=512`、Docker Compose `memory: 512M`、`health.py`运行时监控 |
| A1-06 不导入融合层模块 | ✅ 通过 | 全部17个文件无任何`app.*`或`fusion.*`导入，第三方依赖仅限基础设施类 |
| A1-07 Redis连接池隔离 | ✅ 通过 | `SafetyGatewayRedisPool`使用db=1（URL重写逻辑`redis_pool.py:30`），与主服务db=0隔离 |

### 2.2 可维护性

| 检查项 | 结果 | 说明 |
|--------|------|------|
| C-01 命名一致性 | ✅ 通过 | 模块命名统一（sg_前缀）、类命名清晰（SafetyGatewayChecker/DesensitizationEngine等） |
| C-02 类型注解完整性 | ⚠️ 部分通过 | 公共API有类型注解，但`checker.py`的部分内部方法缺少返回类型注解 |
| C-03 日志规范 | ✅ 通过 | 全部使用`structlog`，日志级别合理（error/warning/info分级清晰） |
| C-04 异常处理规范 | ⚠️ 部分通过 | 大部分使用SG自定义异常，但`main.py:298`和`main.py:334`存在裸`except Exception` |
| C-05 线程安全 | ✅ 通过 | `desensitization.py`使用`threading.Lock`保护规则读写、`checker.py`使用`threading.Lock`保护单例、`circuit_breaker.py`使用`asyncio.Lock`保护状态转换 |

### 2.3 可扩展性

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 规则热更新 | ✅ 通过 | Redis Pub/Sub订阅`desensitization_rules_update`频道，`reload_rules()`支持原子替换+回滚 |
| 阈值热更新 | ✅ 通过 | Redis Pub/Sub订阅`sg:threshold:update`频道，支持refresh/update/delete三种操作 |
| 熔断器可配置 | ✅ 通过 | `CircuitBreakerManager`支持动态创建和参数配置 |
| 白名单可配置 | ✅ 通过 | `safety_gateway_whitelist_fields`通过环境变量可覆盖 |

### 2.4 性能表现

| 检查项 | 结果 | 说明 |
|--------|------|------|
| P99延迟基准 | ✅ 通过 | 性能测试验证P99<5ms、P95<3ms、P50<1ms，使用`time.perf_counter()`、预热50次、禁用GC |
| 脱敏引擎性能 | ✅ 通过 | 单次脱敏P99<1ms |
| 阈值校验性能 | ✅ 通过 | 单次校验P99<0.5ms |
| 禁忌校验性能 | ✅ 通过 | 单次校验P99<0.5ms |
| 并发吞吐量 | ✅ 通过 | 100并发P99<10ms，CI吞吐量≥1000/s |

---

## 3. 代码风格合规性结果

| 检查项 | 评级 | 说明 |
|--------|------|------|
| 命名规范 | A | 模块/类/函数命名一致，sg_前缀区分SG组件 |
| 类型注解 | B+ | 公共API完整，部分内部方法缺失 |
| 日志规范 | A | structlog统一使用，结构化日志字段命名一致 |
| 异常处理 | B | 自定义异常层次完善，但存在2处裸Exception捕获 |
| 文档字符串 | B | 关键模块有文档字符串，但部分方法缺少 |
| 线程安全 | A | 共享状态均有锁保护，快照模式使用正确 |

---

## 4. 功能正确性验证结果

### 4.1 核心校验流程

| 检查项 | 结果 | 说明 |
|--------|------|------|
| A2-01 脱敏校验 | ✅ 通过 | 纯正则匹配+人名词典，11条主规则+4条补丁规则，`[REDACTED:{类型}]`格式 |
| A2-02 禁忌校验 | ✅ 通过 | 按器官（thyroid/liver/cardiac）+全局禁忌，YAML规则+默认规则回退 |
| A2-03 同意状态校验 | ✅ 通过 | 直查PG→Redis降级→E4005B，熔断器保护，指数退避重试 |
| A2-04 一致性校验 | ✅ 通过 | 分级不匹配/发现不匹配/置信度差异>0.3/结论-依据矛盾4种检测 |
| A2-05 阈值校验 | ✅ 通过 | 器官级+病种级阈值，PG动态加载+Redis热更新+默认回退 |
| A2-06 校验执行顺序 | ✅ 通过 | `perform_full_check()`严格按：同意→边缘连接→脱敏→禁忌+阈值→一致性执行，`TestFullCheckIntegration.test_full_check_execution_order`验证 |

### 4.2 降级路径

| 降级场景 | DegradedReason | 测试覆盖 | 结果 |
|----------|----------------|----------|------|
| 场景1: Redis缓存未命中→PG查询成功 | - | ✅ `test_redis_cache_miss_pg_query_success` | 通过 |
| 场景2: 融合层不可用→SG降级输出 | FUSION_UNAVAILABLE | ✅ `test_fusion_unavailable_sg_degraded_output` | 通过 |
| 场景3: SG不可用→全部输出拒绝 | E4005B | ✅ `test_sg_unavailable_reject_all_output` | 通过 |
| 场景4: 边缘后端断连→本地降级 | EDGE_DISCONNECTED | ✅ `test_edge_disconnected_local_degradation` | 通过 |
| 场景5: 组合故障 | COMBINED_FAILURE | ✅ `test_combined_failure_degradation` | 通过 |
| 场景6: 融合层不可用+敏感数据 | FUSION_UNAVAILABLE_SENSITIVE_DETECTED | ✅ `test_fusion_unavailable_sensitive_detected_e4005a` | 通过 |
| 场景7: SG不可用+患者撤回同意 | SG_UNAVAILABLE_CONSENT_WITHDRAWN | ✅ `test_sg_unavailable_consent_withdrawn_e4005a` | 通过 |

### 4.3 边界条件

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 空输出数据 | ✅ 通过 | `perform_full_check()`第314行检查空数据，返回FAILED |
| 递归深度限制 | ✅ 通过 | `MAX_RECURSION_DEPTH=20`，超限返回空列表并记录警告日志 |
| 未知器官阈值 | ✅ 通过 | `_get_threshold()`返回默认0.90 |
| 规则热更新回滚 | ✅ 通过 | `reload_rules()`失败时恢复old_rules和old_patterns |
| 熔断器状态转换 | ✅ 通过 | CLOSED→OPEN(5次失败)→HALF_OPEN(30s冷却)→CLOSED(3次成功) |
| 幻觉严重程度 | ✅ 通过 | `hallucination_severity="severe"`时设置`hallucination_warning=True` |

### 4.4 异常场景

| 检查项 | 结果 | 说明 |
|--------|------|------|
| A3-01 E4005A子码 | ✅ 通过 | E4005A1(DesensitizationSafetyViolationError)、E4005A2(ConsentSafetyViolationError) |
| A3-02 E4005B触发 | ✅ 通过 | PG不可用+Redis缓存未命中→`SGDatabaseUnavailableError` |
| A3-03 E4005C触发 | ✅ 通过 | PG不可用+融合层不可用→`SGCombinedUnavailableError` |
| A3-04 错误码映射 | ✅ 通过 | `ERROR_CODE_MAP`覆盖18个错误码 |

---

## 5. 与参考文档的偏差分析

### 5.1 P0级偏差（必须修复）

| 编号 | 偏差 | 文档要求 | 实际实现 | 影响 |
|------|------|----------|----------|------|
| DD-02 | SG未自主实现融合层健康检查 | 01架构文档："SafetyGateway判定融合层是否可用须基于**进程可达性**（HTTP健康检查`/health`端点），而非基于输出结构中`diagnostic_conclusion`是否为空" | SG通过请求参数`fusion_available`由调用方传入，自身不做融合层健康检查 | **高**：若调用方错误报告融合层状态，SG无法自主验证，可能导致安全校验链断裂 |

### 5.2 P1级偏差（应当修复）

| 编号 | 偏差 | 文档要求 | 实际实现 | 影响 |
|------|------|----------|----------|------|
| DD-03 | 主服务未配置SG主动健康探测 | 01架构文档："主服务每4秒探测SG健康" | Docker Compose中api服务仅通过`depends_on: safety-gateway: condition: service_healthy`保证启动顺序，运行时无主动探测 | **中**：主服务无法及时发现SG运行时故障 |
| DD-04 | Redis缓存TTL配置未使用 | 04安全合规文档："Redis只读缓存（TTL 60s）" | `sg_consent_cache_ttl_seconds=60`配置存在但`consent_checker.py`的`_check_redis_cache()`未使用此值控制缓存读取行为 | **中**：无法保证降级路径的缓存数据时效性 |
| DD-05 | 排队逻辑位置偏差 | 01架构文档："SafetyGateway排队等待PG恢复" | 排队逻辑在客户端`safety_gateway_client.py`而非SG自身 | **低**：功能等价但架构位置不同，需确认是否符合设计意图 |
| QC-01 | `db_query_timeout`默认值不一致 | 00b配置手册：5 | `sg_config.py`：2 | **中**：查询超时过短可能导致正常查询被误判超时 |
| QC-02 | `sg_db_retry_max_attempts`默认值不一致 | 00b配置手册：3 | `sg_config.py`：2 | **中**：重试次数不足可能降低系统韧性 |
| QC-03 | `sg_db_retry_delay_base`默认值不一致 | 00b配置手册：0.5 | `sg_config.py`：0.1 | **低**：退避间隔过短可能导致密集重试 |
| QC-04 | `sg_db_retry_max_total_time_seconds`默认值不一致 | 00b配置手册：10.0 | `sg_config.py`：5.0 | **中**：总重试时间不足可能无法等待PG恢复 |
| QC-06 | 融合层不可用作为active_failures计数 | 设计文档中融合层不可用是独立降级原因 | `checker.py:633`将`not fusion_available`作为active_failures之一参与组合故障判定 | **中**：语义混淆，融合层不可用不应等同于"故障" |

### 5.3 P2级偏差（建议修复）

| 编号 | 偏差 | 说明 |
|------|------|------|
| DD-01 | 代码量超出预估 | 实际~3473行有效代码 vs 预估2500-3000行，超出16% |
| QC-05 | Redis健康检查重置客户端 | `redis_pool.py:57`在健康检查失败时将`_client`设为None，可能导致并发请求重建连接 |
| QC-07 | Redis TTL检查逻辑 | `consent_checker.py:68`的`ttl < 0`判断，Redis返回-2(key不存在)和-1(无过期)语义不同 |
| QC-08 | 阈值DB持久化未等待 | `threshold.py:477-482`通过`asyncio.create_task`执行DB持久化但未等待完成 |

---

## 6. 测试覆盖率分析

### 6.1 测试规模

| 指标 | 实际值 | 设计要求 | 评价 |
|------|--------|----------|------|
| 单元测试方法数 | 227 | 80-120 | **远超预期**（+89%） |
| 测试类数 | 56 | - | 覆盖全面 |
| 性能测试方法数 | 6 | - | 覆盖P99/P95/P50/吞吐量 |

### 6.2 测试覆盖评估

| 覆盖维度 | 评级 | 说明 |
|----------|------|------|
| 脱敏引擎 | A | 26个测试覆盖正则匹配、字典/列表递归、人名词典、补丁规则、规则验证、热更新回滚 |
| 禁忌校验 | A | 12个测试覆盖器官级/全局/禁用规则/临床上下文匹配 |
| 阈值校验 | A | 12个测试覆盖默认值/DB覆盖/动态更新/多器官/边界值 |
| 校验器完整流程 | A | 29个测试覆盖四阶段校验、7种降级场景、入口快速拒绝 |
| 熔断器 | A | 5个测试覆盖三态转换、回退机制、恢复 |
| 健康检查 | A | 完整覆盖Redis/DB/规则引擎/熔断器/阈值引擎/OOM/P99延迟 |
| E4005错误码 | A | 覆盖E4005/E4005A1/E4005A2/E4005B/E4005C |
| 进程隔离 | B+ | 有`TestMainServiceCrashScenario`但缺少Docker级端到端测试 |
| 性能基准 | A | 预热+GC控制+perf_counter+并发测试 |

### 6.3 测试覆盖盲区

| 编号 | 盲区 | 严重程度 | 说明 |
|------|------|----------|------|
| TC-01 | SG未实现融合层健康检查 | P0 | 因功能未实现，无对应测试 |
| TC-02 | Docker容器级进程隔离端到端测试 | P1 | 当前测试均为进程内mock，未验证容器级隔离 |
| TC-03 | Redis Pub/Sub规则热更新端到端集成测试 | P1 | 当前仅mock测试，未验证真实Redis Pub/Sub |
| TC-04 | SG内存预算超限行为测试 | P2 | 无内存超限时的降级行为测试 |
| TC-05 | 禁忌匹配其他数据格式 | P2 | `_match_condition`仅支持2种格式 |
| TC-06 | `_filter_forbidden_terms`与`apply_degraded_output`组合端到端测试 | P2 | 两个功能分别测试但缺少组合验证 |

---

## 7. 发现的问题清单

### 7.1 P0级问题（必须修复）

| 编号 | 问题 | 位置 | 修复建议 |
|------|------|------|----------|
| P0-01 | SG未自主实现融合层健康检查 | `checker.py` | SG应增加对融合层的HTTP健康检查能力（`/health`端点探测），而非完全依赖调用方传入`fusion_available`参数。建议在`HealthChecker`中增加融合层可达性检查，`perform_full_check()`中优先使用自身检查结果 |
| P0-02 | SG未实现融合层健康检查导致测试盲区 | `test_safety_gateway.py` | 需补充SG自主融合层健康检查的测试用例，包括：融合层可达性检测、超时判定、状态缓存 |

### 7.2 P1级问题（应当修复）

| 编号 | 问题 | 位置 | 修复建议 |
|------|------|------|----------|
| P1-01 | `db_query_timeout`默认值2与文档5不一致 | `sg_config.py:27` | 修改默认值为5，与00b配置手册对齐 |
| P1-02 | `sg_db_retry_max_attempts`默认值2与文档3不一致 | `sg_config.py:32` | 修改默认值为3，与00b配置手册对齐 |
| P1-03 | `sg_db_retry_delay_base`默认值0.1与文档0.5不一致 | `sg_config.py:33` | 修改默认值为0.5，与00b配置手册对齐 |
| P1-04 | `sg_db_retry_max_total_time_seconds`默认值5.0与文档10.0不一致 | `sg_config.py:34` | 修改默认值为10.0，与00b配置手册对齐 |
| P1-05 | 主服务未配置SG运行时健康探测 | `docker-compose.dev.yml` | 在api服务中增加对SG的运行时健康探测机制（可通过`SafetyGatewayClient.health_check()`实现定时探测） |
| P1-06 | `sg_consent_cache_ttl_seconds`配置未在降级路径中使用 | `consent_checker.py` | 在`_check_redis_cache()`中检查缓存TTL，超过`sg_consent_cache_ttl_seconds`的缓存数据应视为过期 |
| P1-07 | Redis TTL检查逻辑可能误判 | `consent_checker.py:68` | 修改TTL检查：`ttl == -2`表示key不存在应返回None，`ttl == -1`表示无过期应视为有效，`ttl > 0`时检查是否超过`sg_consent_cache_ttl_seconds` |
| P1-08 | `not fusion_available`作为active_failures计数语义不当 | `checker.py:633` | 融合层不可用是独立降级原因，不应与禁忌/阈值故障组合计数。建议将组合故障判定改为仅统计禁忌+阈值故障，融合层不可用单独处理 |

### 7.3 P2级问题（建议修复）

| 编号 | 问题 | 位置 | 修复建议 |
|------|------|------|----------|
| P2-01 | 代码量超出预估 | `safety_gateway/` | 考虑拆分`checker.py`（909行），将降级输出逻辑提取为独立模块 |
| P2-02 | Redis健康检查重置客户端 | `redis_pool.py:57` | 使用`asyncio.Lock`保护客户端重建，避免并发请求同时触发重建 |
| P2-03 | 阈值DB持久化未等待完成 | `threshold.py:477-482` | 考虑使用`asyncio.gather()`或回调机制确保持久化完成后再返回 |
| P2-04 | `main.py`存在裸Exception捕获 | `main.py:298,334` | 将裸`except Exception`替换为更具体的异常类型 |
| P2-05 | 缺少Docker容器级进程隔离端到端测试 | 测试文件 | 补充Docker Compose环境下的进程隔离测试脚本 |
| P2-06 | 缺少Redis Pub/Sub端到端集成测试 | 测试文件 | 补充使用真实Redis的规则热更新集成测试 |
| P2-07 | 缺少内存预算超限行为测试 | 测试文件 | 补充内存压力下的降级行为测试 |

---

## 8. SafetyGateway轨道修复项声明

以下修复项**显式声明属于SafetyGateway轨道应当进行的修复**：

### 必须修复（P0）

1. **SG-FIX-P0-01：实现融合层自主健康检查**
   - 在`HealthChecker`中增加融合层HTTP健康检查方法
   - 在`perform_full_check()`中优先使用SG自身检查结果，而非完全依赖`fusion_available`参数
   - 保留`fusion_available`参数作为调用方提示，但SG应能自主验证
   - 对应设计文档：01-系统架构设计.md "SafetyGateway判定融合层是否可用须基于进程可达性"

2. **SG-FIX-P0-02：补充融合层健康检查测试**
   - 新增`TestFusionLayerHealthCheck`测试类
   - 覆盖：融合层可达性检测、超时判定、状态缓存、降级触发

### 应当修复（P1）

3. **SG-FIX-P1-01：对齐配置默认值与00b配置手册**
   - 修改`sg_config.py`中4个默认值：`db_query_timeout=5`、`sg_db_retry_max_attempts=3`、`sg_db_retry_delay_base=0.5`、`sg_db_retry_max_total_time_seconds=10.0`

4. **SG-FIX-P1-02：修复Redis缓存TTL检查逻辑**
   - 在`consent_checker.py`的`_check_redis_cache()`中使用`sg_consent_cache_ttl_seconds`配置
   - 修正TTL=-2(不存在)/-1(无过期)的判断逻辑

5. **SG-FIX-P1-03：修正组合故障判定语义**
   - 将`_check_contraindication_threshold_phase()`中的`active_failures`计数改为仅统计禁忌+阈值故障
   - 融合层不可用作为独立降级原因单独处理

6. **SG-FIX-P1-04：主服务增加SG运行时健康探测**
   - 在`SafetyGatewayClient`中增加定时健康探测机制
   - 或在api服务中增加后台任务定期调用SG的`/health`端点

### 建议修复（P2）

7. **SG-FIX-P2-01：拆分checker.py**
   - 将`apply_degraded_output()`和`_filter_forbidden_terms()`提取为`degraded_output.py`模块

8. **SG-FIX-P2-02：增强Redis连接池健壮性**
   - 在`redis_pool.py`中增加`asyncio.Lock`保护客户端重建

9. **SG-FIX-P2-03：补充端到端集成测试**
   - Docker容器级进程隔离测试
   - Redis Pub/Sub规则热更新集成测试

---

## 9. 改进建议与优先级

### 9.1 立即行动（本周）

| 优先级 | 行动项 | 预计工作量 |
|--------|--------|-----------|
| P0 | 实现SG融合层自主健康检查 | 中 |
| P0 | 补充融合层健康检查测试 | 小 |
| P1 | 对齐sg_config.py默认值与00b配置手册 | 极小 |

### 9.2 短期行动（2周内）

| 优先级 | 行动项 | 预计工作量 |
|--------|--------|-----------|
| P1 | 修复Redis缓存TTL检查逻辑 | 小 |
| P1 | 修正组合故障判定语义 | 小 |
| P1 | 主服务增加SG运行时健康探测 | 中 |

### 9.3 中期行动（1个月内）

| 优先级 | 行动项 | 预计工作量 |
|--------|--------|-----------|
| P2 | 拆分checker.py | 中 |
| P2 | 增强Redis连接池健壮性 | 小 |
| P2 | 补充端到端集成测试 | 中 |

---

## 附录A：检查项完整结果矩阵

| 编号 | 检查项 | 结果 | 备注 |
|------|--------|------|------|
| A1-01 | 独立uvicorn实例 | ✅ | |
| A1-02 | 主服务崩溃时独立存活 | ✅ | |
| A1-03 | OOM保护 | ✅ | |
| A1-04 | 健康检查端点 | ✅ | |
| A1-05 | 内存预算 | ✅ | |
| A1-06 | 不导入融合层模块 | ✅ | |
| A1-07 | Redis连接池隔离 | ✅ | |
| A2-01 | 脱敏校验 | ✅ | |
| A2-02 | 禁忌校验 | ✅ | |
| A2-03 | 同意状态校验 | ✅ | |
| A2-04 | 一致性校验 | ✅ | |
| A2-05 | 阈值校验 | ✅ | |
| A2-06 | 校验执行顺序 | ✅ | |
| A3-01 | E4005A子码 | ✅ | |
| A3-02 | E4005B触发 | ✅ | |
| A3-03 | E4005C触发 | ✅ | |
| A3-04 | 错误码映射 | ✅ | |
| B1-01 | SG为第三道防线 | ✅ | |
| B1-02 | 规则异构冗余 | ✅ | SG规则与主规则源正则模式不同 |
| B1-03 | 补丁规则独立维护 | ✅ | 4条补丁规则（护照/医保/车牌/银行卡） |
| B1-04 | NER方案独立性 | ✅ | 纯正则+人名词典，无ML依赖 |
| B2-01 | 输出前权威校验 | ✅ | 直查PG |
| B2-02 | 入口快速拒绝 | ✅ | consent_status_at_input检查 |
| B2-03 | PG不可用降级 | ✅ | Redis只读缓存 |
| B2-04 | Redis缓存未命中 | ✅ | 返回E4005B |
| B3-01 | 7种降级场景 | ✅ | 全部有测试 |
| B3-02 | 降级输出白名单 | ✅ | |
| B3-03 | 降级输出脱敏 | ✅ | |
| B3-04 | 禁忌分级术语过滤 | ✅ | |
| B3-05 | 双重降级场景 | ✅ | desensitization_degraded参数 |
| C-01 | 命名一致性 | ✅ | |
| C-02 | 类型注解 | ⚠️ | 部分内部方法缺失 |
| C-03 | 日志规范 | ✅ | |
| C-04 | 异常处理 | ⚠️ | 2处裸Exception |
| C-05 | 线程安全 | ✅ | |
| D1-01 | 端口配置 | ✅ | 9001 |
| D1-02 | 排队超时约束 | ✅ | 5s < 10s < 15s |
| D1-03 | P99阈值约束 | ✅ | 4.0 < 5.0 |
| D1-04 | 数据库连接池 | ⚠️ | 默认值与文档不一致 |
| D1-05 | 熔断器配置 | ✅ | |
| D2-01 | 测试用例数量 | ✅ | 227个（远超80-120） |
| D2-02 | 降级路径测试 | ✅ | 7种全覆盖 |
| D2-03 | 进程隔离测试 | ⚠️ | 缺少Docker级端到端 |
| D2-04 | OOM保护测试 | ✅ | |
| D2-05 | 性能基准测试 | ✅ | |
| D2-06 | Gate 0验证项 | ⚠️ | 缺少PG直查P99<10ms专项测试 |
| D3-01 | 空输出数据 | ✅ | |
| D3-02 | 递归深度限制 | ✅ | |
| D3-03 | 未知器官阈值 | ✅ | |
| D3-04 | 规则热更新回滚 | ✅ | |
| D3-05 | 熔断器状态转换 | ✅ | |
| D3-06 | 幻觉严重程度 | ✅ | |
| E-01 | Docker Compose SG服务 | ✅ | |
| E-02 | depends_on关系 | ✅ | api依赖safety-gateway(service_healthy) |
| E-03 | 健康检查配置 | ✅ | |
| E-04 | OOM保护 | ✅ | |
| E-05 | Supervisor看门狗 | ✅ | autorestart=true, sguser |
| E-06 | 客户端连接池 | ✅ | httpx.AsyncClient, keep-alive |
| E-07 | 客户端快速失败 | ✅ | FAST_FAIL_THRESHOLD=3 |
| E-08 | 客户端排队等待 | ✅ | _queue_timeout=10s |

**通过率**：52/58 = **89.7%**  
**P0问题**：2个  
**P1问题**：8个  
**P2问题**：7个
