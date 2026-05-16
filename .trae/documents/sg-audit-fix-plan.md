# SafetyGateway轨道审查修复方案与计划

> 本计划仅覆盖SG轨道Phase 0A阶段应修复的项。非SG轨道或非Phase 0A阶段的项留待后续执行。

## 修复范围

根据审查报告，需修复的SG轨道Phase 0A阶段项共15项（P0×2 + P1×6 + P2×7）。

---

## Phase 1: P0严重问题修复（2项）

### [SG-FIX-P0-01] `check_desensitization`递归深度传递不一致

**问题**：`checker.py:77-106`中，`check_desensitization`方法对顶层dict中的嵌套dict/list调用`_check_dict_recursive(item, depth=0)`和`_check_list_recursive(item, depth=0)`，但顶层已处于depth=0，嵌套项应从depth=1开始。同时，顶层list中的嵌套结构也从depth=0开始，与直接传入`_check_dict_recursive`的语义不一致。

**修复方案**：
1. 提取通用方法`_check_value_recursive(value, depth)`，统一处理str/int/float/dict/list类型检测
2. `check_desensitization`入口调用`_check_value_recursive`并传入depth=0
3. `_check_dict_recursive`和`_check_list_recursive`内部调用`_check_value_recursive`并传入depth+1
4. 消除`check_desensitization`中的重复类型判断逻辑

**修改文件**：`safety_gateway/checker.py`

**验证**：运行现有测试`TestRecursionDepthLimit`，确认递归深度限制仍生效

---

### [SG-FIX-P0-02] `ConsentCheckResult`类型访问方式不一致

**问题**：`consent_checker.py`定义了`ConsentCheckResult(NamedTuple)`，但`checker.py`中使用元组解构`(consent_status, is_timeout, is_db_error)`，测试代码中混用`result[2]`和`result.error`。

**修复方案**：
1. `checker.py`中将元组解构改为属性访问：`result.status`/`result.timeout`/`result.error`
2. `_check_consent_phase`方法中所有`ctx.consent_status, ctx.is_timeout, ctx.is_db_error = await ...`改为属性赋值
3. 测试文件中将`result[2]`改为`result.error`

**修改文件**：`safety_gateway/checker.py`、`tests/unit/test_safety_gateway.py`

**验证**：运行`TestDegradedPathScenarios`和`TestConsentChecker`测试类

---

## Phase 2: P1中等问题修复（6项）

### [SG-FIX-P1-01] Dockerfile添加`USER sguser`

**问题**：Dockerfile创建了sguser但CMD未切换用户，容器以root运行。

**修复方案**：在`CMD`行之前添加`USER sguser`

**修改文件**：`Dockerfile.safety-gateway.dev`

**验证**：`docker inspect <container> --format '{{.Config.User}}'`应输出`sguser`

---

### [SG-FIX-P1-02] OOM保护检查增加docker-compose.yml fallback

**问题**：非Linux环境`_check_oom_protection`始终返回unknown。

**修复方案**：
1. 在`_check_oom_protection`中，当`/proc/...`读取失败时，尝试解析docker-compose.dev.yml中safety-gateway服务的oom_score_adj值
2. 返回`{"status": "configured", "oom_score_adj": -1000, "source": "docker-compose", "expected": -1000}`
3. `check_all`中将`configured`状态等同于`protected`

**修改文件**：`safety_gateway/health.py`

**验证**：Windows环境下运行`TestOOMProtectionHealthCheck`测试

---

### [SG-FIX-P1-03] threshold同步/异步加载竞态

**问题**：`load_thresholds`（同步）和`load_thresholds_from_db`（异步）之间无互斥机制。

**修复方案**：
1. 在`load_thresholds`同步方法中，检查`_load_event`是否被清除（表示异步加载正在进行）
2. 若异步加载正在进行，同步方法跳过修改，避免覆盖异步加载的结果
3. 添加注释说明两个方法的互斥关系

**修改文件**：`safety_gateway/threshold.py`

**验证**：运行`TestThresholdEngine`和`TestThresholdHotReload`测试类

---

### [SG-FIX-P1-04] circuit_breaker `_update_state`线程安全

**问题**：`state`和`is_available`属性调用`_update_state`但未持有锁。

**修复方案**：
1. 在`_update_state`方法中添加`asyncio.Lock`保护（使用独立的`_state_lock`避免与`_lock`死锁）
2. 由于`state`和`is_available`是同步属性，改用`threading.Lock`保护状态转换
3. 或者：将`state`/`is_available`改为方法并添加文档说明仅限async上下文使用

**选择方案3**（最小改动）：将`state`和`is_available`保留为property但添加注释说明它们在async上下文中调用是安全的（单线程asyncio模型下无竞态），在`get_stats`等同步方法中添加`_update_state`调用保护。

**修改文件**：`safety_gateway/circuit_breaker.py`

**验证**：运行`TestCircuitBreaker`测试类

---

### [SG-FIX-P1-05] 移除`apply_degraded_output`中不可达分支

**问题**：`FUSION_UNAVAILABLE_SENSITIVE_DETECTED`、`DESENSITIZATION_DEGRADED_SENSITIVE_DETECTED`、`SG_UNAVAILABLE_CONSENT_WITHDRAWN`三个降级原因在`checker.py`中对应`FAILED`状态，不会调用`apply_degraded_output`，因此这三个分支不可达。

**修复方案**：
1. 将三个不可达分支改为`raise ValueError`或`logger.warning`，明确标记为防御性代码
2. 添加注释说明这些分支在当前逻辑下不可达，但保留作为防御性编程

**修改文件**：`safety_gateway/degraded_output.py`

**验证**：运行`TestDegradedOutputIntegrity`测试类

---

### [SG-FIX-P1-06] 移除`database_url`默认值中的硬编码密码

**问题**：`sg_config.py`中`database_url`默认值包含硬编码密码`CHANGEME`。

**修复方案**：
1. 将默认值改为空字符串`""`
2. 在`sg_validator.py`的`validate_sg_config`中添加`database_url`非空校验
3. 启动时若未配置则报错退出

**修改文件**：`safety_gateway/sg_config.py`、`safety_gateway/sg_validator.py`

**验证**：运行`TestPortConflictDetection`测试类，确认配置校验生效

---

## Phase 3: P2低优先级问题修复（7项）

### [SG-FIX-P2-01] 提取`_check_value_recursive`通用方法

**修复方案**：在`checker.py`中新增`_check_value_recursive(self, value, depth)`方法，合并`_check_dict_recursive`和`_check_list_recursive`中的重复逻辑。

**修改文件**：`safety_gateway/checker.py`

---

### [SG-FIX-P2-02] `desensitize_dict`添加递归深度限制

**修复方案**：为`desensitize_dict`和列表处理添加`depth`参数，超过`MAX_RECURSION_DEPTH`时返回原始值。

**修改文件**：`safety_gateway/desensitization.py`

---

### [SG-FIX-P2-03] 修复Windows内存结构体定义

**问题**：`PROCESS_MEMORY_COUNTERS_EX`的`cb`字段应为`c_ulong`（4字节）而非`c_ulonglong`（8字节）。

**修复方案**：将`cb`和`PageFaultCount`字段改为`c_ulong`，与Windows API定义一致。

**修改文件**：`safety_gateway/health.py`

---

### [SG-FIX-P2-04] Redis连接池多事件循环保护

**修复方案**：在`SafetyGatewayRedisPool.get_client`中检测当前事件循环，若与lock绑定的事件循环不同则重建lock。

**修改文件**：`safety_gateway/redis_pool.py`

---

### [SG-FIX-P2-05] 禁忌匹配添加语义说明注释

**修复方案**：在`_match_condition`方法中添加注释说明当前仅支持精确字符串匹配，规则条件为预定义枚举值。

**修改文件**：`safety_gateway/contraindication.py`

---

### [SG-FIX-P2-06] 测试断言改用属性访问

**修复方案**：将`test_safety_gateway.py`中所有`result[0]`/`result[1]`/`result[2]`改为`result.status`/`result.timeout`/`result.error`。

**修改文件**：`tests/unit/test_safety_gateway.py`

---

### [SG-FIX-P2-07] 重写客户端排队逻辑测试

**修复方案**：重写`TestSafetyGatewayClientQueueLogic`三个测试，模拟实际的请求失败/成功路径，验证`_consecutive_failures`在实际逻辑中的变化。

**修改文件**：`tests/unit/test_safety_gateway.py`

---

## 执行顺序

```
Phase 1 (P0): SG-FIX-P0-01 → SG-FIX-P0-02 → 运行全量测试
Phase 2 (P1): SG-FIX-P1-01 → P1-02 → P1-03 → P1-04 → P1-05 → P1-06 → 运行全量测试
Phase 3 (P2): SG-FIX-P2-01 → P2-02 → P2-03 → P2-04 → P2-05 → P2-06 → P2-07 → 运行全量测试
```

每个Phase完成后运行全量测试确认无回归。

## 验证标准

1. 每个修复项完成后，对应的测试用例必须通过
2. 每个Phase完成后，运行`pytest tests/unit/test_safety_gateway.py -v`全量测试
3. 所有Phase完成后，运行`pytest tests/benchmark/test_safety_gateway_performance.py -v`性能测试
4. 最终确认P99 < 5ms基准不受影响
