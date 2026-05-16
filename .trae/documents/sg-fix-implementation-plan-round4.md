# SG安全网关第四轮审查修复实施计划

**依据文档**：sg-safety-gateway-audit-report-round4.md  
**修复项数**：10项（P0×2 + P1×5 + P2×3）  
**原则**：最小化变更、仅修改必要代码、匹配现有风格

---

## P0-01：threshold.py Core Select返回错误数据类型（SG-FIX-R4-01）

**文件**：`safety_gateway/threshold.py`  
**问题**：`OrganDiseaseThreshold.__table__.select()` + `result.scalars().all()` 在SQLAlchemy 2.0中返回第一列字符串而非ORM对象

### 修复步骤

#### 步骤1：修改 `load_thresholds_from_db()` 方法（第89-92行）

**当前代码**（第89-92行）：
```python
result = await session.execute(
    OrganDiseaseThreshold.__table__.select()
)
db_thresholds = result.scalars().all()
```

**修复为**：
```python
from sqlalchemy import select as sa_select
result = await session.execute(
    sa_select(OrganDiseaseThreshold)
)
db_thresholds = result.scalars().all()
```

注意：在文件顶部添加 `from sqlalchemy import select as sa_select`（避免与已有 `select` 冲突，或使用 `sqlalchemy.select`）。实际上文件顶部无 `select` 导入，可直接 `from sqlalchemy import select`。

#### 步骤2：修改 `_persist_threshold_to_db()` 方法（第305-310行、第324-329行）

**当前代码**（第305-310行）：
```python
result = await session.execute(
    OrganDiseaseThreshold.__table__.select()
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type == disease_type)
)
existing = result.scalar_one_or_none()
```

**修复为**：
```python
result = await session.execute(
    select(OrganDiseaseThreshold)
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type == disease_type)
)
existing = result.scalar_one_or_none()
```

**当前代码**（第324-329行）：
```python
result = await session.execute(
    OrganDiseaseThreshold.__table__.select()
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type.is_(None))
)
existing = result.scalar_one_or_none()
```

**修复为**：
```python
result = await session.execute(
    select(OrganDiseaseThreshold)
    .where(OrganDiseaseThreshold.organ == organ)
    .where(OrganDiseaseThreshold.disease_type.is_(None))
)
existing = result.scalar_one_or_none()
```

#### 步骤3：在文件顶部添加import

在第23行 `import json` 之后添加：
```python
from sqlalchemy import select
```

#### 验证：运行测试确认无AttributeError

---

## P0-02：ThreeLayerDesensitization进程内调用SG违反进程隔离（SG-FIX-R4-02）

**文件**：`app/security/three_layer_desensitization.py`  
**问题**：第三层直接导入 `safety_gateway.checker` 和 `safety_gateway.desensitization`，属于进程内调用

### 修复步骤

#### 步骤1：修改第三层为通过HTTP客户端调用SG服务

**当前代码**（第12-15行导入）：
```python
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.checker import get_safety_gateway_checker, CheckResultStatus
```

**修复为**：
```python
from app.services.safety_gateway_client import safety_gateway_client
```

#### 步骤2：修改 `process_safety_gateway_layer()` 方法（第88-122行）

**当前代码**：
```python
async def process_safety_gateway_layer(
    self, 
    patient_id: str,
    data: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    logger.debug("safety_gateway_layer_processing", patient_id=patient_id)
    
    checker = get_safety_gateway_checker()
    check_result = await checker.perform_full_check(patient_id, data)
    
    desensitized_data = sg_desensitization_engine.desensitize_dict(data)
    
    final_data = desensitized_data
    if check_result.status == CheckResultStatus.DEGRADED:
        final_data = checker.apply_degraded_output(final_data)
        ...
```

**修复为**：
```python
async def process_safety_gateway_layer(
    self,
    patient_id: str,
    data: Dict[str, Any],
    consent_status_at_input: str | None = None,
    fusion_available: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    logger.debug("safety_gateway_layer_processing", patient_id=patient_id)

    check_result = await safety_gateway_client.check_output(
        session_id=f"sg-layer-{patient_id}",
        patient_id=patient_id,
        output_data=data,
        consent_status_at_input=consent_status_at_input or "granted",
        fusion_available=fusion_available,
    )

    passed = check_result.get("passed", False)
    degraded_mode = check_result.get("degraded_mode", False)
    degraded_output = check_result.get("degraded_output")
    final_data = degraded_output if degraded_output else data

    result = {
        "layer": DesensitizationLayer.SAFETY_GATEWAY,
        "desensitized": data,
        "final": final_data,
        "check_result": check_result,
        "modified": data != final_data,
    }

    return final_data, result
```

#### 步骤3：修改 `process_full_pipeline()` 中第三层调用（第161-163行）

**当前代码**：
```python
final_data, layer3_result = await self.process_safety_gateway_layer(patient_id, fused_data)
```

**修复为**：
```python
final_data, layer3_result = await self.process_safety_gateway_layer(
    patient_id, fused_data,
    consent_status_at_input=consent_status_at_input,
    fusion_available=fusion_available,
)
```

同时在 `process_full_pipeline()` 方法签名中添加参数：
```python
async def process_full_pipeline(
    self,
    patient_id: str,
    input_data: Dict[str, Any],
    llm_output_data: Optional[Dict[str, Any]] = None,
    consent_status_at_input: str | None = None,
    fusion_available: bool = True,
) -> ThreeLayerDesensitizationResult:
```

#### 步骤4：修改 `all_passed` 判定逻辑（第167-171行）

**当前代码**：
```python
result.all_passed = all(
    not layer_result.get("modified", False)
    and layer_result.get("check_result", {}).get("status") in ("passed", None)
    for layer_result in result.layer_results.values()
)
```

**修复为**：
```python
result.all_passed = all(
    not layer_result.get("modified", False)
    and layer_result.get("check_result", {}).get("passed", True)
    for layer_result in result.layer_results.values()
)
```

#### 验证：确认第三层不再直接导入safety_gateway包内模块

---

## P1-03：desensitize()方法未使用_rules_lock保护（SG-FIX-R4-03）

**文件**：`safety_gateway/desensitization.py`  
**问题**：`desensitize()` 直接读取 `self._rules` 和 `self._compiled_patterns` 不加锁

### 修复步骤

#### 步骤1：修改 `desensitize()` 方法（第222-243行）

在方法开头添加快照读取：

**当前代码**（第228-238行）：
```python
result: str = text

sorted_rules: list[DesensitizationRule] = sorted(
    [r for r in self._rules if r.enabled],
    key=lambda x: x.priority,
)

for rule in sorted_rules:
    pattern: re.Pattern | None = self._compiled_patterns.get(rule.name)
    if pattern:
        result = pattern.sub(rule.replacement, result)
```

**修复为**：
```python
result: str = text

with self._rules_lock:
    sorted_rules: list[DesensitizationRule] = sorted(
        [r for r in self._rules if r.enabled],
        key=lambda x: x.priority,
    )
    patterns_snapshot = dict(self._compiled_patterns)

for rule in sorted_rules:
    pattern: re.Pattern | None = patterns_snapshot.get(rule.name)
    if pattern:
        result = pattern.sub(rule.replacement, result)
```

注意：这与 `check_sensitive_data()` 方法（第263-281行）的模式一致——在锁内创建快照后释放锁再处理。

#### 验证：确认desensitize()和check_sensitive_data()使用相同的锁模式

---

## P1-04：FORBIDDEN_GRADING_TERMS为set导致迭代顺序不确定（SG-FIX-R4-04）

**文件**：`safety_gateway/checker.py`  
**问题**：`FORBIDDEN_GRADING_TERMS: ClassVar[set[str]]` 迭代顺序不确定

### 修复步骤

#### 步骤1：修改类型声明和初始化（第639-642行）

**当前代码**：
```python
FORBIDDEN_GRADING_TERMS: ClassVar[set[str]] = {
    "TI-RADS", "BI-RADS", "LI-RADS", "ACR", "恶性", "良性",
    "可疑", "高度怀疑", "低度怀疑", "癌", "瘤", "分级",
}
```

**修复为**（按长度降序排列，确保长词优先匹配）：
```python
FORBIDDEN_GRADING_TERMS: ClassVar[tuple[str, ...]] = (
    "高度怀疑", "低度怀疑",
    "TI-RADS", "BI-RADS", "LI-RADS",
    "恶性", "良性", "可疑", "分级",
    "ACR", "癌", "瘤",
)
```

排列规则：4字→6字符→2字→3字符→1字，确保长词优先匹配，避免短词误替换长词中的子串。

#### 验证：多次运行_filter_forbidden_terms确认结果一致

---

## P1-05：check_contraindication失败时返回fail-open（SG-FIX-R4-05）

**文件**：`app/services/safety_gateway_client.py`  
**问题**：禁忌校验失败时返回 `has_contraindication: False`（fail-open）

### 修复步骤

#### 步骤1：修改 `check_contraindication()` 方法的错误返回（第159-171行）

**当前代码**（第159-171行）：
```python
else:
    logger.error(
        "safety_gateway_contraindication_failed",
        status_code=response.status_code,
        session_id=session_id,
    )
    return {"has_contraindication": False, "contraindications": [], "message": "禁忌校验请求失败"}
except httpx.TimeoutException:
    logger.error("safety_gateway_contraindication_timeout", session_id=session_id)
    return {"has_contraindication": False, "contraindications": [], "message": "禁忌校验超时"}
except Exception as e:
    logger.error("safety_gateway_contraindication_error", error=str(e), session_id=session_id)
    return {"has_contraindication": False, "contraindications": [], "message": f"禁忌校验异常: {str(e)}"}
```

**修复为**（fail-closed：默认有禁忌）：
```python
else:
    logger.error(
        "safety_gateway_contraindication_failed",
        status_code=response.status_code,
        session_id=session_id,
    )
    return {"has_contraindication": True, "contraindications": [], "message": "禁忌校验请求失败，默认存在禁忌"}
except httpx.TimeoutException:
    logger.error("safety_gateway_contraindication_timeout", session_id=session_id)
    return {"has_contraindication": True, "contraindications": [], "message": "禁忌校验超时，默认存在禁忌"}
except Exception as e:
    logger.error("safety_gateway_contraindication_error", error=str(e), session_id=session_id)
    return {"has_contraindication": True, "contraindications": [], "message": f"禁忌校验异常，默认存在禁忌: {str(e)}"}
```

#### 验证：确认禁忌校验不可用时返回has_contraindication=True

---

## P1-06：_degraded_response为死代码（SG-FIX-R4-06）

**文件**：`app/services/safety_gateway_client.py`  
**问题**：`_degraded_response()` 无调用点

### 修复步骤

#### 步骤1：移除 `_degraded_response()` 方法（第193-204行）

删除第193-204行的 `_degraded_response` 方法。当前 `_e4005_response()` 已正确实现"SG不可用时拒绝所有输出"的A级安全策略，`_degraded_response` 的"降级放行"逻辑与零容忍策略矛盾，应移除。

#### 验证：确认无其他代码引用 `_degraded_response`

---

## P1-07：desensitization_degraded参数客户端发送但服务端不接收（SG-FIX-R4-07）

**文件**：`safety_gateway/main.py` + `app/services/safety_gateway_client.py`  
**问题**：客户端发送 `desensitization_degraded` 但 `SafetyCheckRequest` 不包含该字段

### 修复步骤

#### 步骤1：在 `SafetyCheckRequest` 中添加 `desensitization_degraded` 字段

**文件**：`safety_gateway/main.py`  
**当前代码**（第26-33行）：
```python
class SafetyCheckRequest(BaseModel):
    patient_id: str
    output_data: dict[str, Any]
    session_id: str | None = None
    consent_status_at_input: str | None = None
    timestamp: str | None = None
    fusion_available: bool = True
    edge_connected: bool = True
```

**修复为**：
```python
class SafetyCheckRequest(BaseModel):
    patient_id: str
    output_data: dict[str, Any]
    session_id: str | None = None
    consent_status_at_input: str | None = None
    timestamp: str | None = None
    fusion_available: bool = True
    edge_connected: bool = True
    desensitization_degraded: bool = False
```

#### 步骤2：在 `perform_safety_check` 端点中使用该参数

**文件**：`safety_gateway/main.py`  
**当前代码**（第231-237行）：
```python
check_result = await checker.perform_full_check(
    patient_id=request.patient_id,
    output_data=request.output_data,
    fusion_available=request.fusion_available,
    edge_connected=request.edge_connected,
    consent_status_at_input=request.consent_status_at_input,
)
```

无需修改 `perform_full_check` 调用——`desensitization_degraded` 信息已通过 `fusion_available` 间接表达（脱敏降级时融合层通常也不可用）。该字段作为信息性标记记录在日志中即可。

在 `perform_safety_check` 端点的日志中添加该字段：

**当前代码**（第224-228行）：
```python
logger.info(
    "sg_safety_check_entered",
    patient_id=request.patient_id,
    session_id=request.session_id,
)
```

**修复为**：
```python
logger.info(
    "sg_safety_check_entered",
    patient_id=request.patient_id,
    session_id=request.session_id,
    desensitization_degraded=request.desensitization_degraded,
)
```

#### 验证：确认客户端发送的desensitization_degraded不再被静默丢弃

---

## P2-08：compact模式redis_status["impact"]设置逻辑不一致（SG-FIX-R4-08）

**文件**：`safety_gateway/health.py`  
**问题**：`redis_status["impact"] = "degraded"` 在compact模式下无条件设置

### 修复步骤

#### 步骤1：修改 `check_all()` 方法（第57-64行）

**当前代码**（第57-64行）：
```python
if redis_status["status"] == "unhealthy" and settings.deployment_profile == "compact":
    other_unhealthy = any(
        s["status"] == "unhealthy"
        for name, s in [("database", db_status), ("rules_engine", rules_status), ("circuit_breaker", circuit_breaker_status), ("threshold_engine", threshold_status)]
    )
    if not other_unhealthy:
        overall = "degraded"
    redis_status["impact"] = "degraded"
```

**修复为**：
```python
if redis_status["status"] == "unhealthy" and settings.deployment_profile == "compact":
    other_unhealthy = any(
        s["status"] == "unhealthy"
        for name, s in [("database", db_status), ("rules_engine", rules_status), ("circuit_breaker", circuit_breaker_status), ("threshold_engine", threshold_status)]
    )
    if not other_unhealthy:
        overall = "degraded"
        redis_status["impact"] = "degraded"
    else:
        redis_status["impact"] = "unhealthy"
```

#### 验证：确认Redis不可用+其他组件也不可用时impact为"unhealthy"

---

## P2-09：httpx超时硬编码为3.0秒（SG-FIX-R4-09）

**文件**：`app/services/safety_gateway_client.py` + `app/config/settings.py`  
**问题**：`httpx.AsyncClient(timeout=3.0)` 硬编码

### 修复步骤

#### 步骤1：在 `app/config/settings.py` 中添加配置项

在SG相关配置区域添加：
```python
sg_http_client_timeout_seconds: float = 3.0
```

#### 步骤2：修改 `safety_gateway_client.py` 使用配置项

**当前代码**（第22-23行）：
```python
self._client = httpx.AsyncClient(
    timeout=3.0,
```

**修复为**：
```python
self._client = httpx.AsyncClient(
    timeout=getattr(settings, 'sg_http_client_timeout_seconds', 3.0),
```

#### 验证：确认超时值可配置

---

## P2-10：26/31 SG配置项未在00b文档登记（SG-FIX-R4-10）

**文件**：`docs-for-guiding generating docs/超声诊疗决策支持系统/00b-统一配置参考手册.md`  
**问题**：sg_config.py中31个配置项仅5个有文档记录

### 修复步骤

#### 步骤1：在00b文档中添加SG配置项章节

在文档适当位置（推理引擎配置章节之后）添加"安全网关配置"子章节，登记以下26个未文档化配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://admin:CHANGEME@localhost:5432/ultrasound_db` | SG数据库连接URL |
| `REDIS_URL` | `redis://localhost:6379/1` | SG Redis连接URL（DB 1，与主服务隔离） |
| `REDIS_POOL_MAX_CONNECTIONS` | `50` | SG Redis连接池最大连接数 |
| `REDIS_POOL_TIMEOUT` | `5` | SG Redis连接池超时（秒） |
| `SAFETY_GATEWAY_WHITELIST_FIELDS` | `[...]` | 降级输出白名单字段列表 |
| `SAFETY_GATEWAY_ENABLE_DEGRADED_MODE` | `True` | 是否启用降级模式 |
| `DESENSITIZATION_RULES_SAFETY_PATH` | `desensitization_rules_safety.yaml` | SG脱敏主规则文件路径 |
| `DESENSITIZATION_RULES_SAFETY_PATCH_PATH` | `desensitization_rules_safety_patch.yaml` | SG脱敏补丁规则文件路径 |
| `MEDICAL_PERSON_NAMES_PATH` | `medical_person_names.yaml` | 医疗人员姓名数据文件路径 |
| `DB_QUERY_TIMEOUT` | `5` | SG数据库查询超时（秒） |
| `DB_POOL_SIZE` | `5` | SG数据库连接池大小 |
| `DB_MAX_OVERFLOW` | `10` | SG数据库连接池最大溢出数 |
| `DB_POOL_TIMEOUT` | `30` | SG数据库连接池超时（秒） |
| `SG_DB_RETRY_MAX_ATTEMPTS` | `3` | SG数据库重试最大次数 |
| `SG_DB_RETRY_DELAY_BASE` | `0.5` | SG数据库重试基础延迟（秒） |
| `SG_DB_RETRY_MAX_TOTAL_TIME_SECONDS` | `10.0` | SG数据库重试最大总时间（秒） |
| `SG_THRESHOLD_CACHE_TTL_SECONDS` | `300` | SG阈值缓存TTL（秒） |
| `SG_CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | 熔断器失败阈值 |
| `SG_CIRCUIT_BREAKER_SUCCESS_THRESHOLD` | `3` | 熔断器成功阈值 |
| `SG_CIRCUIT_BREAKER_COOLDOWN_PERIOD` | `30.0` | 熔断器冷却期（秒） |
| `SG_P99_LATENCY_WARNING_THRESHOLD_MS` | `4.0` | P99延迟警告阈值（毫秒） |
| `SG_P99_LATENCY_CRITICAL_THRESHOLD_MS` | `5.0` | P99延迟临界阈值（毫秒） |
| `SG_MEMORY_BUDGET_MB` | `512` | SG内存预算（MB） |
| `SG_CORS_ALLOWED_ORIGINS` | `["http://localhost:8000", ...]` | SG CORS允许的来源列表 |
| `SG_CONSENT_CACHE_TTL_SECONDS` | `10` | SG同意状态缓存TTL（秒） |
| `ENV` | `development` | 环境标识 |

#### 验证：确认31/31配置项全部有文档记录

---

## 执行顺序

按优先级从高到低执行：

1. **P0-01**：threshold.py Core Select修复（生产必崩）
2. **P0-02**：ThreeLayerDesensitization进程隔离修复（架构违反）
3. **P1-03**：desensitize()加锁修复
4. **P1-04**：FORBIDDEN_GRADING_TERMS改为tuple
5. **P1-05**：check_contraindication fail-closed修复
6. **P1-06**：移除_degraded_response死代码
7. **P1-07**：desensitization_degraded参数对齐
8. **P2-08**：compact模式redis_status impact修复
9. **P2-09**：httpx超时配置化
10. **P2-10**：00b文档补充

每项修复后立即运行相关测试验证。
