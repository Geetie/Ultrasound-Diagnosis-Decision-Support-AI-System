# SG安全网关R10修复实施计划

**依据文档**：sg-safety-gateway-audit-report-round10.md
**修复范围**：12项修复（3 P0 + 6 P1 + 3 P2），逐项落实，不得遗漏

---

## 修复项总览

| 编号 | 严重程度 | 文件 | 修复类型 |
|------|----------|------|----------|
| SG-FIX-R10-01 | P0 | main.py + checker.py | 代码修复 |
| SG-FIX-R10-02 | P0 | main.py | 代码修复 |
| SG-FIX-R10-03 | P0 | checker.py | 代码修复 |
| SG-FIX-R10-04 | P1 | checker.py | 代码修复 |
| SG-FIX-R10-05 | P1 | errors.py | 代码修复 |
| SG-FIX-R10-06 | P1 | sg_config.py | 代码修复 |
| SG-FIX-R10-07 | P1 | main.py | 代码修复 |
| SG-FIX-R10-08 | P1 | degraded_output.py | 代码修复 |
| SG-FIX-R10-09 | P1 | sg_validator.py | 代码修复 |
| SG-FIX-R10-10 | P2 | 06-开发环境与进度规划.md | 文档更新 |
| SG-FIX-R10-11 | P2 | 06-开发环境与进度规划.md | 文档更新 |
| SG-FIX-R10-12 | P2 | redis_pool.py | 代码修复 |

---

## SG-FIX-R10-01：main.py /api/v1/consent-status 端点调用不存在的方法

**问题**：`SafetyGatewayChecker`没有`check_consent_status`方法，且`ConsentChecker.check_consent_status`返回`ConsentCheckResult` NamedTuple而非三元组。

**修复方案**：

### 步骤1：在checker.py的SafetyGatewayChecker类中添加代理方法

在`SafetyGatewayChecker`类中（`apply_degraded_output`方法之后，`get_whitelist_fields`方法之前）添加：

```python
async def check_consent_status(self, patient_id: str) -> ConsentCheckResult:
    """代理方法：查询患者同意状态，委托给ConsentChecker"""
    return await self._consent_checker.check_consent_status(patient_id)
```

需要在checker.py顶部添加import：
```python
from safety_gateway.consent_checker import ConsentCheckResult, get_consent_checker
```

### 步骤2：修改main.py第237行的解包方式

将：
```python
consent_status, is_timeout, is_db_error = await checker.check_consent_status(patient_id)
```
改为：
```python
result = await checker.check_consent_status(patient_id)
consent_status = result.status
is_timeout = result.timeout
is_db_error = result.error
```

### 验证标准
- `SafetyGatewayChecker.check_consent_status`方法存在且返回`ConsentCheckResult`
- main.py `/api/v1/consent-status`端点不再抛出AttributeError
- `ConsentCheckResult`的三个字段正确解包到`consent_status`/`is_timeout`/`is_db_error`

---

## SG-FIX-R10-02：main.py /ready 端点引用不存在的键名"checks"

**问题**：`HealthChecker.check_all()`返回`components`键而非`checks`键，导致`/ready`始终返回503。

**修复方案**：

修改main.py第213-216行，将：
```python
core_ok = result.get("checks", {}).get("database", {}).get("status") == "ok" and result.get("checks", {}).get("rules_engine", {}).get("status") == "ok"
if core_ok and result["status"] in ("healthy", "degraded"):
    return JSONResponse(content={"status": "ready", "checks": result.get("checks", {})}, status_code=200)
return JSONResponse(content={"status": "not_ready", "checks": result.get("checks", {})}, status_code=503)
```
改为：
```python
components = result.get("components", {})
core_ok = components.get("database", {}).get("status") == "healthy" and components.get("rules_engine", {}).get("status") == "healthy"
if core_ok and result["status"] in ("healthy", "degraded"):
    return JSONResponse(content={"status": "ready", "components": components}, status_code=200)
return JSONResponse(content={"status": "not_ready", "components": components}, status_code=503)
```

**关键点**：
- 键名从`"checks"`改为`"components"`（对齐health.py第116行）
- 状态值从`"ok"`改为`"healthy"`（对齐health.py中各组件的status值）

### 验证标准
- `/ready`端点在database和rules_engine均为healthy时返回200
- `/ready`端点在任一核心组件unhealthy时返回503
- 返回的JSON中使用`components`键而非`checks`键

---

## SG-FIX-R10-03：checker.py E4005A2路径不可达

**问题**：`is_db_error=True`时`consent_status=None`，导致`consent_status == ConsentStatus.WITHDRAWN`永远为False，E4005A2路径不可达。

**修复方案**：

修改checker.py的`_check_consent_phase`方法签名，添加`consent_status_at_input`参数，结合入口阶段的同意状态判断E4005A2场景。

### 步骤1：修改_check_consent_phase方法签名

将：
```python
async def _check_consent_phase(
    self,
    patient_id: str,
    fusion_available: bool,
    ctx: _PhaseContext,
    start_time: datetime,
) -> CheckResult | None:
```
改为：
```python
async def _check_consent_phase(
    self,
    patient_id: str,
    fusion_available: bool,
    ctx: _PhaseContext,
    start_time: datetime,
    consent_status_at_input: str | None = None,
) -> CheckResult | None:
```

### 步骤2：修改E4005A2判断逻辑

将第396-409行：
```python
if ctx.is_db_error:
    if ctx.consent_status == ConsentStatus.WITHDRAWN:
        ...
```
改为：
```python
if ctx.is_db_error:
    if consent_status_at_input == "withdrawn":
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        logger.error("sg_unavailable_consent_withdrawn_db_error", patient_id=patient_id)
        return CheckResult(
            status=CheckResultStatus.FAILED,
            consent_status=ConsentStatus.WITHDRAWN,
            sensitive_data_detected=[],
            message="安全网关不可用且患者已撤回知情同意，拒绝所有诊断输出（E4005A2）",
            degraded_mode=True,
            degraded_reason=DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN,
            elapsed_ms=round(elapsed, 2),
            consent_check_timeout=False,
        )
```

### 步骤3：修改perform_full_check中的调用

在perform_full_check方法中，将_check_consent_phase调用从：
```python
result = await self._check_consent_phase(patient_id, effective_fusion_available, ctx, start_time)
```
改为：
```python
result = await self._check_consent_phase(patient_id, effective_fusion_available, ctx, start_time, consent_status_at_input=consent_status_at_input)
```

### 验证标准
- 当DB不可用且`consent_status_at_input="withdrawn"`时，触发E4005A2
- 当DB不可用且`consent_status_at_input`为其他值/None时，触发E4005B
- 入口快速拒绝阶段（consent_status_at_input非granted）仍正常工作

---

## SG-FIX-R10-04：checker.py E4005B错误消息仍引用"Redis缓存未命中"

**修复方案**：

修改checker.py第417-420行，将：
```python
raise SGDatabaseUnavailableError(
    error_message="数据库查询异常且Redis缓存未命中，无法获取同意状态（E4005B）",
    detail="sg_db_unavailable_redis_miss",
)
```
改为：
```python
raise SGDatabaseUnavailableError(
    error_message="数据库查询异常，无法获取同意状态（E4005B）",
    detail="sg_db_unavailable",
)
```

### 验证标准
- E4005B错误消息不再包含"Redis缓存未命中"
- detail字段从"sg_db_unavailable_redis_miss"改为"sg_db_unavailable"

---

## SG-FIX-R10-05：errors.py DatabaseUnavailableError文档字符串仍引用"Redis缓存未命中"

**修复方案**：

修改errors.py第169-172行，将：
```python
class DatabaseUnavailableError(SafetyGatewayError):
    """数据库不可用，Redis缓存未命中"""
    code = "E4005B"
    message = "数据库不可用，无法获取同意状态"
```
改为：
```python
class DatabaseUnavailableError(SafetyGatewayError):
    """数据库不可用，无法获取同意状态"""
    code = "E4005B"
    message = "数据库不可用，无法获取同意状态"
```

### 验证标准
- DatabaseUnavailableError的docstring与message字段一致
- 不再引用"Redis缓存未命中"

---

## SG-FIX-R10-06：sg_config.py sg_consent_cache_ttl_seconds为死配置

**修复方案**：

从sg_config.py第49行移除`sg_consent_cache_ttl_seconds`配置项。

将第49行：
```python
    sg_consent_cache_ttl_seconds: int = 10
```
删除（整行移除）。

### 验证标准
- sg_config.py中不再包含`sg_consent_cache_ttl_seconds`
- 全项目无代码引用此配置项（已确认consent_checker.py不使用）

---

## SG-FIX-R10-07：main.py降级输出未传递fusion_available参数

**问题**：main.py第293行`apply_degraded_output`调用未传递`fusion_available`参数，默认为True，但FUSION_UNAVAILABLE/COMBINED_FAILURE场景下融合层实际不可用。

**修复方案**：

修改main.py第291-293行，从HealthChecker获取融合层可用性状态并传递：

将：
```python
if check_result.degraded_mode:
    reason = check_result.degraded_reason or DegradedReason.SENSITIVE_DATA_DETECTED
    response.degraded_output = checker.apply_degraded_output(request.output_data, reason=reason)
```
改为：
```python
if check_result.degraded_mode:
    reason = check_result.degraded_reason or DegradedReason.SENSITIVE_DATA_DETECTED
    from safety_gateway.checker import _get_health_checker
    _hc = _get_health_checker()
    _fusion_available = await _hc.get_fusion_availability()
    effective_fusion = _fusion_available if _fusion_available is not None else False
    response.degraded_output = checker.apply_degraded_output(request.output_data, reason=reason, fusion_available=effective_fusion)
```

### 验证标准
- FUSION_UNAVAILABLE场景下降级输出的`fusion_available=False`
- SENSITIVE_DATA_DETECTED场景下降级输出的`fusion_available`反映实际状态
- 不影响其他降级场景的正确性

---

## SG-FIX-R10-08：degraded_output.py FUSION_UNAVAILABLE_SENSITIVE_DETECTED分支safety_gateway_unavailable=True语义不正确

**修复方案**：

修改degraded_output.py第135-140行，将：
```python
elif reason == DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED:
    logger.info("sg_degraded_output_defensive_branch", reason=reason.value)
    degraded_data["fusion_degraded"] = True
    degraded_data["safety_gateway_unavailable"] = True
    degraded_data["fusion_unavailable_sensitive_detected"] = True
    degraded_data["error_code"] = "E4005A1"
```
改为：
```python
elif reason == DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED:
    logger.info("sg_degraded_output_defensive_branch", reason=reason.value)
    degraded_data["fusion_degraded"] = True
    degraded_data["safety_gateway_unavailable"] = False
    degraded_data["fusion_unavailable"] = True
    degraded_data["fusion_unavailable_sensitive_detected"] = True
    degraded_data["error_code"] = "E4005A1"
```

**关键变更**：
- `safety_gateway_unavailable`从`True`改为`False`（SG本身可用，是融合层不可用）
- 新增`fusion_unavailable=True`标记（明确标记融合层不可用）

### 验证标准
- FUSION_UNAVAILABLE_SENSITIVE_DETECTED场景下`safety_gateway_unavailable=False`
- 新增`fusion_unavailable=True`标记
- 与COMBINED_FAILURE分支的修正模式一致（R9-05修复）

---

## SG-FIX-R10-09：sg_validator.py缺少e2e P99阈值校验规则

**修复方案**：

在sg_validator.py的`validate_sg_config`函数中，在第43行（现有P99校验之后）添加e2e阈值校验：

在第43行之后添加：
```python
    if sg_settings.sg_e2e_p99_latency_warning_threshold_ms <= 0:
        errors.append("SG_E2E_P99_LATENCY_WARNING_THRESHOLD_MS must be greater than 0")

    if sg_settings.sg_e2e_p99_latency_critical_threshold_ms <= 0:
        errors.append("SG_E2E_P99_LATENCY_CRITICAL_THRESHOLD_MS must be greater than 0")

    if sg_settings.sg_e2e_p99_latency_warning_threshold_ms >= sg_settings.sg_e2e_p99_latency_critical_threshold_ms:
        errors.append("SG_E2E_P99_LATENCY_WARNING_THRESHOLD_MS must be less than SG_E2E_P99_LATENCY_CRITICAL_THRESHOLD_MS")
```

### 验证标准
- e2e阈值校验规则与现有P99阈值校验逻辑对称
- warning < critical的约束被校验
- 正值约束被校验

---

## SG-FIX-R10-10：代码量超出设计文档预估

**修复方案**：更新06-开发环境与进度规划.md中的代码量预估。

**具体操作**：在文档中找到代码量预估位置，将"约2,500-3,000行"更新为"约4,000-4,500行"。

### 验证标准
- 文档中的代码量预估与实际代码量一致

---

## SG-FIX-R10-11：测试用例超出设计文档预估

**修复方案**：更新06-开发环境与进度规划.md中的测试用例预估。

**具体操作**：在文档中找到测试用例预估位置，将"约80-120个"更新为"约200-250个"。

### 验证标准
- 文档中的测试用例预估与实际测试用例数量一致

---

## SG-FIX-R10-12：redis_pool.py health_check中使用未声明的类变量_consecutive_failures

**修复方案**：

在redis_pool.py的`SafetyGatewayRedisPool`类中，在现有类变量声明之后（第36行`_lock_loop_id`之后）添加：

```python
    _consecutive_failures: ClassVar[int] = 0
```

同时修改health_check方法第91行，将：
```python
            cls._consecutive_failures = 0
```
保持不变（已有显式声明后此赋值不再创建动态属性）。

修改第94行，将：
```python
            cls._consecutive_failures = getattr(cls, '_consecutive_failures', 0) + 1
```
改为：
```python
            cls._consecutive_failures += 1
```

### 验证标准
- `_consecutive_failures`在类定义中显式声明为`ClassVar[int] = 0`
- 不再使用`getattr`的默认值模式
- health_check逻辑行为不变

---

## 实施顺序

按文件分组，减少文件切换：

1. **checker.py**（R10-01步骤1 + R10-03 + R10-04）
2. **main.py**（R10-01步骤2 + R10-02 + R10-07）
3. **errors.py**（R10-05）
4. **sg_config.py**（R10-06）
5. **degraded_output.py**（R10-08）
6. **sg_validator.py**（R10-09）
7. **redis_pool.py**（R10-12）
8. **06-开发环境与进度规划.md**（R10-10 + R10-11）

## 验证步骤

修复完成后执行：
1. 运行`python -c "from safety_gateway import SafetyGatewayChecker"`验证导入
2. 运行`python -c "from safety_gateway.checker import ConsentCheckResult"`验证新类型导入
3. 运行现有测试套件验证无回归
