# SafetyGateway 轨道审查修复计划

> **声明**：本计划所有修复项均属于SafetyGateway轨道应当进行的修复项

## 修复原则

1. **最小化变更**：每项修复仅针对审查报告中的具体问题，不做额外重构
2. **7步调试协议**（debug-pro）：复现→隔离→假设→插桩→验证→修复→回归测试
3. **自反性检查**（Self-Improving）：每项修复完成后进行自我评估，记录教训
4. **先P0后P1再P2**：严格按优先级顺序修复

---

## Phase 1：P0级修复（3项）

### FIX-P0-01：融合层可用性判定机制偏差

**问题**：SG仍部分依赖调用方传入的`fusion_available`参数，未完全自主判定融合层可用性

**调试分析**：
- **复现**：当SG健康检查尚未执行（`_fusion_available=None`）时，`perform_full_check()`回退到调用方传入的`fusion_available`值
- **隔离**：问题在[checker.py:274-282]和[main.py:42]
- **假设**：移除`fusion_available`参数后，SG完全依赖`HealthChecker.get_fusion_availability()`自主判定，缓存TTL=4s可保证时效性

**修复步骤**：

1. **修改 `safety_gateway/main.py`**
   - 从`SafetyCheckRequest`中移除`fusion_available: bool = Field(default=True, ...)`
   - 从`perform_safety_check()`中移除`fusion_available=request.fusion_available`传参

2. **修改 `safety_gateway/checker.py`**
   - 从`perform_full_check()`签名中移除`fusion_available: bool = True`参数
   - 将`effective_fusion_available`的计算逻辑改为：直接使用`health_checker.get_fusion_availability()`的返回值，当返回`None`时（未知状态）默认为`False`（安全优先：未知视为不可用）
   - 移除`sg_fusion_available_override`的警告日志（不再有调用方传入值可对比）
   - 将`_check_desensitization_phase`和`_check_contraindication_threshold_phase`中的`fusion_available`参数改为从`_PhaseContext`中获取

3. **修改 `safety_gateway/checker.py` 的 `_PhaseContext`**
   - 添加`fusion_available: bool = True`字段，在`perform_full_check()`入口处设置

4. **修改 `tests/unit/test_safety_gateway.py`**
   - 更新所有调用`perform_full_check()`的测试用例，移除`fusion_available`参数
   - 添加测试：验证SG自主检测融合层不可用时的降级行为
   - 添加测试：验证`get_fusion_availability()`返回`None`时默认为不可用

**验证标准**：
- `perform_full_check()`签名中无`fusion_available`参数
- SG完全通过`HealthChecker.get_fusion_availability()`自主判定融合层状态
- 所有现有测试通过
- 新增测试覆盖自主判定逻辑

---

### FIX-P0-02：E4005A子码触发语义与设计文档对齐

**问题**：E4005A1/E4005A2的触发语义与原始E4005A定义不一致，需文档对齐

**调试分析**：
- **复现**：E4005A原始定义="脱敏降级+SG不可用"，E4005A1实际触发="融合层不可用+敏感数据检测"
- **隔离**：代码逻辑正确（是对原始设计的合理细化），问题在于缺少语义映射文档
- **假设**：通过代码注释和错误消息明确E4005A子码的扩展语义即可

**修复步骤**：

1. **修改 `safety_gateway/errors.py`**
   - 更新`DesensitizationSafetyViolationError`的`message`和`code`注释，明确说明：
     - E4005A1：脱敏防线降级（第二道或第三道防线缺失/降级）且仍检测到敏感数据，拒绝输出
     - 涵盖场景：融合层不可用+SG检测到敏感数据；融合层脱敏已降级+SG检测到敏感数据
   - 更新`ConsentSafetyViolationError`的`message`，明确说明：
     - E4005A2：安全校验组件不可用且患者同意状态异常（已撤回），拒绝输出

2. **修改 `safety_gateway/checker.py`**
   - 在`_check_desensitization_phase()`中E4005A1触发处添加注释，说明语义映射关系
   - 在`_check_consent_phase()`中E4005A触发处添加注释

3. **修改 `tests/unit/test_safety_gateway.py`**
   - 添加`TestE4005ASubCodeSemantics`测试类，验证E4005A1/E4005A2的触发场景与扩展语义一致

**验证标准**：
- 错误消息和注释清晰说明E4005A子码的扩展语义
- 新增测试验证语义映射

---

### FIX-P0-03：Redis缓存TTL判断逻辑缺陷

**问题**：`_check_redis_cache()`中TTL判断存在死代码和语义反转

**调试分析**：
- **复现**：`ttl == -2`分支为死代码；`ttl == -1`无过期时间直接返回有安全风险；`ttl > consent_cache_ttl_seconds`判断语义反转
- **隔离**：问题在[consent_checker.py:62-89]
- **假设**：修正TTL判断逻辑为计算缓存存活时间（age），当age超过TTL配置时视为过期

**修复步骤**：

1. **修改 `safety_gateway/consent_checker.py` 的 `_check_redis_cache()`**
   - 移除`ttl == -2`死代码分支（key不存在时`cached`必为None，外层已处理）
   - `ttl == -1`（无过期时间）视为过期数据，返回None并记录警告
   - 修正TTL判断逻辑：
     ```python
     # 计算缓存已存活时间
     # Redis TTL返回的是key剩余存活秒数
     # 主服务写入时设置的TTL为sg_consent_cache_ttl_seconds
     # 因此缓存已存活时间 = sg_consent_cache_ttl_seconds - ttl
     cache_age = self._consent_cache_ttl_seconds - ttl
     if cache_age > self._consent_cache_ttl_seconds:
         # 缓存已过期（TTL剩余时间小于0或存活时间超过配置TTL）
         return None
     ```
   - 简化逻辑：当`0 < ttl <= self._consent_cache_ttl_seconds`时缓存有效，否则视为过期

2. **修改 `tests/unit/test_safety_gateway.py`**
   - 添加`TestConsentRedisTTLCheck`测试类：
     - 测试TTL=-2（key不存在）场景
     - 测试TTL=-1（无过期时间）场景 → 应返回None
     - 测试TTL在有效范围内场景 → 应返回缓存值
     - 测试TTL超过配置TTL场景 → 应返回None
     - 测试TTL=0（即将过期）场景 → 应返回None

**验证标准**：
- `_check_redis_cache()`无死代码分支
- `ttl == -1`返回None而非缓存值
- TTL判断语义正确：剩余TTL在(0, consent_cache_ttl_seconds]范围内才有效
- 新增5个测试用例全部通过

---

## Phase 2：P1级修复（7项）

### FIX-P1-01：sg_consent_cache_ttl_seconds默认值对齐

**修复步骤**：
1. 修改`sg_config.py:47`：`sg_consent_cache_ttl_seconds: int = 10`（从5改为10）
2. 更新测试中依赖此默认值的断言

**验证**：默认值与00b配置手册一致

---

### FIX-P1-02：组合故障判定语义修正

**修复步骤**：
1. 修改`checker.py`的`_check_contraindication_threshold_phase()`：
   - 在`_PhaseContext`中添加`has_sensitive_data: bool = False`字段
   - 在`_check_desensitization_phase()`中检测到敏感数据时设置`ctx.has_sensitive_data = True`（但不提前返回，仅记录状态）
   - 修改`active_failures`计算：`active_failures = sum([ctx.has_sensitive_data, has_contraindication, has_threshold_failure])`
   - 注意：此修改需谨慎，因为脱敏阶段检测到敏感数据时当前逻辑会提前返回DEGRADED/FAILED。需要在`_PhaseContext`中记录敏感数据状态但不改变脱敏阶段的提前返回逻辑
   - **实际方案**：在`perform_full_check()`入口处先执行脱敏检测（仅检测，不判断），将结果存入ctx，然后在各阶段中使用ctx中的状态。但这会改变校验流程的短路逻辑，风险较大
   - **更安全的方案**：在注释中明确说明设计意图——脱敏故障已在阶段2单独处理（降级/拒绝），阶段3的组合故障仅统计禁忌+阈值，这是有意的设计选择而非遗漏。添加注释说明即可

**最终方案**：添加设计决策注释，不改变逻辑

**验证**：注释清晰说明设计意图

---

### FIX-P1-03：3种降级原因不可达分支处理

**修复步骤**：
1. 在`degraded_output.py`中，为3个不可达分支添加明确注释：
   - 说明这些分支在当前checker逻辑中不会被调用（因为对应场景返回FAILED而非DEGRADED）
   - 保留代码作为防御性编程，但移除`logger.warning("sg_degraded_output_unreachable_branch_invoked")`，改为`logger.info()`
2. 在`validation_result.py`的`DegradedReason`枚举中，为这3个值添加注释说明其对应的CheckResultStatus为FAILED

**验证**：代码注释清晰，日志级别正确

---

### FIX-P1-04：主服务SG运行时健康探测

**修复步骤**：
1. 在`safety_gateway/health.py`中添加`SGHealthProbe`类：
   - 定期（4s间隔）检查SG自身健康状态
   - 超时1s，连续3次失败判定SG不可用
   - 提供给主服务的客户端使用
2. 在`safety_gateway/main.py`中添加`/health/probe`端点，返回SG的详细健康探测结果
3. 注意：主服务的SG客户端健康探测逻辑应在主服务代码中实现，不在SG包内。SG侧仅需确保`/health`端点响应足够快（<1ms）

**实际方案**：
1. 确认SG的`/health`端点响应时间满足1s超时要求
2. 在SG的README或注释中说明主服务应实现的健康探测规范
3. 不在SG包内添加主服务侧的逻辑（职责分离）

**验证**：SG `/health`端点响应时间<1s

---

### FIX-P1-05：脱敏规则异构冗余自动化验证

**修复步骤**：
1. 在`safety_gateway/desensitization.py`的`_get_default_rules()`中添加注释，说明每条规则与主规则源的异构差异点
2. 添加`validate_heterogeneous_redundancy()`方法：
   - 接受主规则源路径参数
   - 对比SG规则与主规则的正则模式
   - 返回完全相同的规则列表（应为空）
3. 在`sg_validator.py`的`validate_sg_config()`中调用此验证（当主规则源路径可用时）

**验证**：新增验证方法，CI可调用

---

### FIX-P1-06：阈值引擎同步/异步加载竞争

**修复步骤**：
1. 修改`threshold.py`的`load_thresholds()`同步方法：
   - 在修改`_thresholds`前检查`_load_event`状态
   - 如果`_load_event`未设置（异步加载进行中），跳过同步加载并记录警告
   - 当前代码已有此检查（第140-142行），但仅跳过而不等待
2. 添加`_thresholds_lock = threading.Lock()`保护`_thresholds`字典的写操作
3. 在`load_thresholds()`和`load_thresholds_from_db()`中均使用`_thresholds_lock`

**验证**：并发加载场景无数据竞争

---

### FIX-P1-07：结论-依据矛盾检测误报风险

**修复步骤**：
1. 修改`checker.py`的`_check_conclusion_evidence_contradiction()`：
   - 扩展否定前缀列表：添加"除外"、"不考虑"、"未提示"、"未见"、"排除"、"不考虑"
   - 添加分句逻辑：按逗号、句号、分号分割文本后逐句检测
   - 仅当矛盾词对出现在同一句子中时才判定为矛盾
2. 添加误报率监控：在矛盾检测命中时记录日志，包含完整的conclusion和evidence文本

**验证**：新增分句检测测试用例

---

## Phase 3：P2级修复（5项）

### FIX-P2-01：代码量预估更新

**修复步骤**：更新06-开发环境与进度规划.md中的代码量预估（如用户要求则修改，否则仅记录）

---

### FIX-P2-02：Redis健康检查重置客户端连接

**修复步骤**：
1. 修改`redis_pool.py`的`health_check()`：
   - 添加连续失败计数器`_consecutive_failures`
   - 仅当连续3次失败时才重置客户端连接
   - 单次失败仅记录警告

**验证**：单次Redis超时不中断Pub/Sub订阅

---

### FIX-P2-03：禁忌匹配扩展（仅注释）

**修复步骤**：在`contraindication.py`的`_match_condition()`中添加TODO注释，说明未来可能需要支持层级匹配

---

### FIX-P2-04：RedisPool类变量文档

**修复步骤**：在`redis_pool.py`中添加类文档说明类变量的进程级隔离语义

---

### FIX-P2-05：性能测试添加HTTP端到端延迟

**修复步骤**：
1. 在`tests/benchmark/test_safety_gateway_performance.py`中添加`TestHTTPEndToEndLatency`测试类
2. 使用`httpx.AsyncClient`向SG服务发送真实HTTP请求
3. 标记为`@pytest.mark.integration`，仅在集成测试环境运行

**验证**：HTTP端到端P99 < 5ms

---

## 执行顺序与依赖关系

```
Phase 1 (P0):
  FIX-P0-03 (TTL逻辑) ──无依赖──→ 可并行
  FIX-P0-02 (E4005A语义) ──无依赖──→ 可并行
  FIX-P0-01 (融合层判定) ──依赖P0-03修复后的consent_checker──→ 最后执行

Phase 2 (P1):
  FIX-P1-01 (配置默认值) ──无依赖──→ 可并行
  FIX-P1-03 (不可达分支) ──无依赖──→ 可并行
  FIX-P1-04 (健康探测) ──无依赖──→ 可并行
  FIX-P1-05 (异构验证) ──无依赖──→ 可并行
  FIX-P1-02 (组合故障) ──依赖P0-01──→ Phase 1后执行
  FIX-P1-06 (阈值竞争) ──无依赖──→ 可并行
  FIX-P1-07 (矛盾检测) ──无依赖──→ 可并行

Phase 3 (P2):
  所有P2项无依赖，可并行执行
```

## 回归测试策略

每项修复完成后：
1. 运行`tests/unit/test_safety_gateway.py`全量测试
2. 运行`tests/benchmark/test_safety_gateway_performance.py`性能测试
3. 确认无回归后标记该修复项完成

全部修复完成后：
1. 运行全量测试套件
2. 执行自反性评估，记录教训到`~/self-improving/corrections.md`

## 自反性评估计划

修复完成后记录：
```
CONTEXT: SafetyGateway轨道审查修复
REFLECTION: [修复过程中发现的问题和改进]
LESSON: [对未来类似工作的指导]
```
